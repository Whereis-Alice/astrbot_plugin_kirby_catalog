"""QQ routing, rich delivery fallback, bounded reading and saved draw safety."""

import hashlib
import json
import time
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from astrbot.api import message_components as Comp
from astrbot_plugin_kirby_catalog import main as main_module
from astrbot_plugin_kirby_catalog import qq_official as qq
from astrbot_plugin_kirby_catalog.main import AllyDrawOutcome
from botpy.errors import ForbiddenError, ServerError
from PIL import Image

from .test_main import FakeEvent, FakeStore, make_plugin

ENTRY = {
    "id": 12,
    "filename": "Waddle_Dee.png",
    "name": "瓦豆鲁迪",
    "page_title": "Waddle Dee",
}


class OfficialEvent(FakeEvent):
    def __init__(
        self,
        command="今日盟友",
        *,
        group="group-openid",
        user="user-openid",
        platform="qq_official",
        app="app-1",
    ):
        super().__init__(command, group_id=group, sender_id=user, sender_name="狐狸")
        self.platform = platform
        self.platform_meta = SimpleNamespace(id="official-1", name=platform)
        self.unified_msg_origin = (
            f"official-1:{'GroupMessage' if group else 'FriendMessage'}:{group or user}"
        )
        self.message_obj.raw_message = SimpleNamespace(
            group_openid=group, author=SimpleNamespace(user_openid=user)
        )
        self.message_obj.message_id = "incoming-id"
        self.bot = SimpleNamespace(
            api=SimpleNamespace(
                _http=SimpleNamespace(
                    _token=SimpleNamespace(app_id=app), request=AsyncMock()
                ),
                post_group_message=AsyncMock(),
                post_message=AsyncMock(),
                post_dms=AsyncMock(),
            )
        )
        self.upload_group_and_c2c_image = AsyncMock(
            return_value={"file_info": "platform-file"}
        )

    def get_platform_name(self):
        return self.platform

    def get_platform_id(self):
        return self.platform_meta.id


class Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status, self.data, self.headers = (
            status,
            data or {"id": "sent-id"},
            headers or {},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return self.data

    def raise_for_status(self):
        if self.status >= 400:
            raise qq.APIError(self.status)


class RichDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.delivery = qq.QQOfficialDelivery(self.root)
        self.event = OfficialEvent()
        self.image_path = self.root / "card.png"
        Image.new("RGB", (200, 310), "pink").save(self.image_path)
        self.image = Comp.Image.fromFileSystem(str(self.image_path))
        self.outcome = AllyDrawOutcome(
            ENTRY, 4, False, False, collected=37, total=1353, daily_limit=5
        )
        self.view = qq.draw_presentation(
            self.outcome, "狐狸", "瓦豆鲁迪", is_bot=False, limit=5, wiki=True
        )
        self.chain = [Comp.Plain("旧配文不能重复"), self.image]

    async def test_three_step_upload_checks_digests_and_cleans_own_image(self):
        data = self.image_path.read_bytes()
        md5 = hashlib.md5(data).hexdigest()
        calls = []

        async def request(_api, route, **kwargs):
            calls.append((route.method, route.path, kwargs))
            if route.path.endswith("prepare"):
                return {
                    "upload_id": "upload-1",
                    "presigned_url": "https://bucket.cos.ap-guangzhou.myqcloud.com/image?signature=example",
                }
            if route.path.endswith("complete"):
                return {
                    "image_id": "own-image",
                    "url": "qqbot-media://image/v1/example",
                }
            return {"id": "sent-card"}

        cos = MagicMock()
        cos.__aenter__ = AsyncMock(return_value=cos)
        cos.__aexit__ = AsyncMock(return_value=False)
        cos.put.return_value = Response(headers={"ETag": f'"{md5}"'})
        with (
            patch.object(qq, "_request", side_effect=request),
            patch.object(qq.aiohttp, "ClientSession", return_value=cos),
        ):
            receipts = await self.delivery.send(self.event, self.chain, self.view)
        self.assertEqual(receipts, ["sent-card"])
        prepare = calls[0][2]["json"]
        self.assertEqual(prepare["md5"], md5)
        self.assertEqual(prepare["sha1"], hashlib.sha1(data).hexdigest())
        self.assertEqual(prepare["content_type"], "image/png")
        self.assertEqual(prepare["file_size"], len(data))
        self.assertEqual(
            cos.put.call_args.kwargs["headers"], {"Content-Type": "image/png"}
        )
        self.assertFalse(cos.put.call_args.kwargs["allow_redirects"])
        payload = calls[2][2]["json"]
        self.assertIn("qqbot-media://image/v1/example", payload["markdown"]["content"])
        self.assertNotIn("旧配文", payload["markdown"]["content"])
        self.assertIn("狐狸", payload["markdown"]["content"])
        self.assertTrue(payload["markdown"]["content"].startswith("狐狸，你今天的盟友是"))
        self.assertNotIn("##", payload["markdown"]["content"])
        self.assertEqual(
            calls[-1][0:2], ("DELETE", "/v2/media-images/{image_id}/delete")
        )
        self.assertEqual(
            json.loads(self.delivery._owned_path.read_text())[qq.bot_key(self.event)],
            [],
        )

    async def test_bad_signed_url_is_never_contacted(self):
        for url in (
            "http://bucket.myqcloud.com/x",
            "https://evil.example/x",
            "https://cos.myqcloud.com.evil.example/x",
            "https://user:pass@bucket.myqcloud.com/x",
        ):
            with (
                self.subTest(url=url),
                patch.object(
                    qq,
                    "_request",
                    AsyncMock(return_value={"presigned_url": url, "upload_id": "up"}),
                ),
                patch.object(qq.aiohttp, "ClientSession") as cos,
            ):
                with self.assertRaises(ValueError):
                    await self.delivery._upload(self.event, self.image, [])
                cos.assert_not_called()

    async def test_wrong_etag_never_completes(self):
        cos = MagicMock()
        cos.__aenter__ = AsyncMock(return_value=cos)
        cos.__aexit__ = AsyncMock(return_value=False)
        cos.put.return_value = Response(headers={"ETag": "different"})
        request = AsyncMock(
            return_value={
                "presigned_url": "https://bucket.myqcloud.com/x",
                "upload_id": "up",
            }
        )
        with (
            patch.object(qq, "_request", request),
            patch.object(qq.aiohttp, "ClientSession", return_value=cos),
        ):
            with self.assertRaises(ValueError):
                await self.delivery._upload(self.event, self.image, [])
        self.assertEqual(request.await_count, 1)

    async def test_invalid_image_never_uploads(self):
        bad = self.root / "fake.png"
        bad.write_text("not an image")
        with patch.object(qq, "_request") as request:
            with self.assertRaises(OSError):
                await self.delivery._upload(
                    self.event, Comp.Image.fromFileSystem(str(bad)), []
                )
            request.assert_not_called()

    async def test_image_size_limit_precedes_read(self):
        with (
            patch.object(
                Path, "stat", return_value=SimpleNamespace(st_size=11 * 1024 * 1024)
            ),
            patch.object(Path, "read_bytes") as reader,
        ):
            with self.assertRaises(ValueError):
                self.delivery._image_data(str(self.image_path))
            reader.assert_not_called()

    async def test_buttons_rejected_retry_only_markdown(self):
        post = AsyncMock(
            side_effect=[qq.APIError(400, 40034025), {"id": "markdown-only"}]
        )
        with (
            patch.object(
                self.delivery,
                "_upload",
                AsyncMock(return_value=("qqbot-media://image/v1/example", 200, 310)),
            ),
            patch.object(self.delivery, "_post", post),
            patch.object(self.delivery, "_attachment", AsyncMock()) as attached,
        ):
            await self.delivery.send(self.event, self.chain, self.view)
        self.assertEqual(post.await_count, 2)
        self.assertNotIn("keyboard", post.call_args.args[1])
        self.assertIn(
            qq.escape("查看简介 #12"), post.call_args.args[1]["markdown"]["content"]
        )
        attached.assert_not_called()

    async def test_legacy_forbidden_is_definite_rejection(self):
        self.assertTrue(qq._rejected(ForbiddenError("permission denied")))
        self.assertFalse(qq._rejected(ServerError("unknown status")))
        self.assertFalse(qq._rejected(qq.APIError(500)))
        self.assertFalse(qq._rejected(qq.APIError(429)))

    async def test_markdown_denied_falls_back_to_saved_caption_and_image(self):
        post = AsyncMock(
            side_effect=[qq.APIError(403), qq.APIError(403), {"id": "plain"}]
        )
        with (
            patch.object(
                self.delivery,
                "_upload",
                AsyncMock(return_value=("qqbot-media://image/v1/x", 200, 310)),
            ),
            patch.object(self.delivery, "_post", post),
            patch.object(
                self.delivery, "_attachment", AsyncMock(return_value="attached")
            ) as attached,
        ):
            receipts = await self.delivery.send(self.event, self.chain, self.view)
        self.assertEqual(receipts, ["attached", "plain"])
        self.assertEqual(attached.await_count, 1)
        self.assertIn(
            "狐狸，你今天的盟友是 瓦豆鲁迪", post.call_args.args[1]["content"]
        )
        self.assertIn("4/5 次", post.call_args.args[1]["content"])

    async def test_upload_unavailable_uses_one_attachment_even_when_markdown_denied(
        self,
    ):
        post = AsyncMock(
            side_effect=[qq.APIError(403), qq.APIError(403), {"id": "plain"}]
        )
        with (
            patch.object(
                self.delivery, "_upload", AsyncMock(side_effect=qq.APIError(404))
            ),
            patch.object(self.delivery, "_post", post),
            patch.object(
                self.delivery, "_attachment", AsyncMock(return_value="attached")
            ) as attached,
        ):
            await self.delivery.send(self.event, self.chain, self.view)
        self.assertEqual(attached.await_count, 1)

    async def test_ambiguous_timeout_does_not_retry_or_attach(self):
        post = AsyncMock(side_effect=TimeoutError())
        with (
            patch.object(
                self.delivery,
                "_upload",
                AsyncMock(return_value=("qqbot-media://image/v1/x", 200, 310)),
            ),
            patch.object(self.delivery, "_post", post),
            patch.object(self.delivery, "_attachment", AsyncMock()) as attached,
        ):
            with self.assertRaises(TimeoutError):
                await self.delivery.send(self.event, self.chain, self.view)
        self.assertEqual(post.await_count, 1)
        attached.assert_not_called()

    async def test_ambiguous_error_during_retry_does_not_plain_fallback(self):
        post = AsyncMock(
            side_effect=[qq.APIError(403), aiohttp.ClientConnectionError()]
        )
        with (
            patch.object(self.delivery, "_post", post),
            patch.object(self.delivery, "_attachment", AsyncMock()) as attached,
        ):
            with self.assertRaises(aiohttp.ClientConnectionError):
                await self.delivery.send(self.event, [Comp.Plain("简介")], self.view)
        self.assertEqual(post.await_count, 2)
        attached.assert_not_called()

    async def test_cleanup_failure_does_not_hide_success(self):
        async def upload(_event, _image, owned):
            owned.append("own-image")
            return "qqbot-media://image/v1/x", 200, 310

        with (
            patch.object(self.delivery, "_upload", side_effect=upload),
            patch.object(self.delivery, "_post", AsyncMock(return_value={"id": "ok"})),
            patch.object(
                self.delivery, "_delete_owned", AsyncMock(side_effect=OSError())
            ),
        ):
            self.assertEqual(
                await self.delivery.send(self.event, self.chain, self.view), ["ok"]
            )

    async def test_cleanup_only_deletes_this_bots_recorded_ids(self):
        key = qq.bot_key(self.event)
        self.delivery._save_owned({key: ["ours"], "other-bot": ["theirs"]})
        request = AsyncMock(return_value={})
        with patch.object(qq, "_request", request):
            await self.delivery._delete_owned(
                self.event.bot.api, key, ["ours", "theirs", "../invalid"]
            )
        self.assertEqual(request.await_count, 1)
        self.assertEqual(request.call_args.args[1].parameters["image_id"], "ours")
        self.assertEqual(self.delivery._load_owned()["other-bot"], ["theirs"])

    async def test_active_image_cannot_be_deleted_by_another_request(self):
        key = qq.bot_key(self.event)
        self.delivery._save_owned({key: ["active"]})
        self.delivery._active_owned.add((key, "active"))
        with patch.object(qq, "_request", AsyncMock()) as request:
            await self.delivery._delete_owned(self.event.bot.api, key, ["active"])
            request.assert_not_called()
            await self.delivery._delete_owned(
                self.event.bot.api, key, ["active"], newly_uploaded=True
            )
            self.assertEqual(request.await_count, 1)

    async def test_failed_cleanup_remains_for_retry_and_expired_is_removed(self):
        key = qq.bot_key(self.event)
        self.delivery._save_owned({key: ["old"]})
        with patch.object(qq, "_request", AsyncMock(side_effect=qq.APIError(500))):
            await self.delivery._delete_owned(self.event.bot.api, key, ["old"])
        self.assertEqual(self.delivery._load_owned()[key], ["old"])
        with patch.object(
            qq, "_request", AsyncMock(side_effect=qq.APIError(400, 40035008))
        ):
            await self.delivery._delete_owned(self.event.bot.api, key, ["old"])
        self.assertEqual(self.delivery._load_owned()[key], [])

    async def test_newly_completed_image_can_be_cleaned_after_registry_write_failure(
        self,
    ):
        with (
            patch.object(self.delivery, "_load_owned", return_value={}),
            patch.object(qq, "_request", AsyncMock(return_value={})) as request,
        ):
            await self.delivery._delete_owned(
                self.event.bot.api,
                qq.bot_key(self.event),
                ["just-uploaded"],
                newly_uploaded=True,
            )
        self.assertEqual(request.await_count, 1)

    async def test_disabled_and_template_buttons_include_manual_commands(self):
        for options in (
            {"buttons_enabled": False},
            {"keyboard_template": "approved-template"},
        ):
            with (
                self.subTest(options=options),
                patch.object(
                    self.delivery, "_post", AsyncMock(return_value={"id": "ok"})
                ) as post,
            ):
                await self.delivery.send(
                    self.event, [Comp.Plain("简介")], self.view, **options
                )
                payload = post.call_args.args[1]
                self.assertIn(qq.escape("查看简介 #12"), payload["markdown"]["content"])
                if not options.get("buttons_enabled", True):
                    self.assertNotIn("keyboard", payload)
                else:
                    self.assertEqual(payload["keyboard"], {"id": "approved-template"})

    async def test_markdown_template_receives_full_body_under_configured_key(self):
        with patch.object(
            self.delivery, "_post", AsyncMock(return_value={"id": "ok"})
        ) as post:
            await self.delivery.send(
                self.event,
                [Comp.Plain("完整简介")],
                qq.Presentation(),
                markdown_mode="模板 Markdown",
                markdown_template="template-1",
                template_key="body",
            )
        payload = post.call_args.args[1]["markdown"]
        self.assertEqual(payload["custom_template_id"], "template-1")
        self.assertEqual(payload["params"][0]["key"], "body")
        self.assertIn("完整简介", payload["params"][0]["values"][0])

    async def test_nonofficial_and_unsupported_component_leave_standard_sender(self):
        self.assertIsNone(await self.delivery.send(FakeEvent("今日盟友"), self.chain))
        self.assertIsNone(
            await self.delivery.send(self.event, [Comp.Record(file="/record.mp3")])
        )

    async def test_html_attachment_explains_supported_text_alternative(self):
        with patch.object(
            self.delivery, "_post", AsyncMock(return_value={"id": "ok"})
        ) as post:
            await self.delivery.send(
                self.event, [Comp.File(name="guide.html", file="/guide.html")]
            )
        body = post.call_args.args[1]["markdown"]["content"]
        self.assertIn("暂不支持发送本地 HTML 附件", body)
        self.assertIn("卡比百科文本", body)

    async def test_reading_image_snapshot_survives_gallery_overwrite(self):
        pages = await self.delivery._snapshot_pages([[self.image], [self.image]])
        snapshot = Path(await pages[0][0].convert_to_file_path())
        original = self.image_path.read_bytes()
        Image.new("RGB", (200, 310), "blue").save(self.image_path)
        self.assertEqual(snapshot.read_bytes(), original)
        self.assertEqual(await pages[1][0].convert_to_file_path(), str(snapshot))

    async def test_long_reading_sends_one_page_and_next_page_keeps_images(self):
        components = [
            Comp.Plain("简介。" * 1800),
            self.image,
            Comp.Plain("尾段"),
            self.image,
        ]
        with (
            patch.object(
                self.delivery, "_post", AsyncMock(return_value={"id": "ok"})
            ) as post,
            patch.object(
                self.delivery,
                "_upload",
                AsyncMock(return_value=("qqbot-media://image/v1/x", 200, 310)),
            ) as upload,
        ):
            await self.delivery.send(self.event, components)
            self.assertEqual(post.await_count, 1)
            token = next(iter(self.delivery.readings))
            reading = self.delivery.readings[token]
            rebuilt = "".join(
                item.text
                for page in reading.pages
                for item in page
                if isinstance(item, Comp.Plain)
            )
            self.assertEqual(rebuilt, "简介。" * 1800 + "尾段")
            self.assertEqual(
                sum(
                    isinstance(item, Comp.Image)
                    for page in reading.pages
                    for item in page
                ),
                2,
            )
            self.assertIn("下一页", str(post.call_args.args[1]["keyboard"]))
            await self.delivery.send(
                self.event, [], token=token, page=len(reading.pages)
            )
            self.assertEqual(upload.await_count, 1)
            self.assertIn("上一页", str(post.call_args.args[1]["keyboard"]))


class RoutingAndReadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_routes_group_c2c_channel_and_dms(self):
        destinations = [
            (OfficialEvent(), "/v2/groups/group-openid/messages", True),
            (OfficialEvent(group=""), "/v2/users/user-openid/messages", True),
        ]
        channel = OfficialEvent()
        channel.message_obj.raw_message = SimpleNamespace(channel_id="channel-1")
        destinations.append((channel, "/channels/channel-1/messages", False))
        DirectMessage = type("DirectMessage", (), {})
        direct = OfficialEvent()
        direct.message_obj.raw_message = DirectMessage()
        direct.message_obj.raw_message.guild_id = "guild-1"
        destinations.append((direct, "/dms/guild-1/messages", False))
        for event, path, msg_type in destinations:
            with (
                self.subTest(path=path),
                patch.object(
                    qq, "_request", AsyncMock(return_value={"id": "ok"})
                ) as request,
            ):
                await qq.QQOfficialDelivery._post(
                    event, {"msg_type": 2, "markdown": {"content": "example"}}
                )
                self.assertTrue(request.call_args.args[1].url.endswith(path))
                body = request.call_args.kwargs["json"]
                self.assertEqual("msg_type" in body, msg_type)
                self.assertEqual("msg_seq" in body, msg_type)
                self.assertEqual(body["msg_id"], "incoming-id")
                self.assertTrue(event._has_send_oper)

    async def test_authenticated_request_preserves_400_and_never_retries(self):
        session = MagicMock()
        session.request.return_value = Response(
            400, {"code": 40034025, "message": "sensitive payload"}
        )
        api = SimpleNamespace(
            _http=SimpleNamespace(
                check_session=AsyncMock(),
                _session=session,
                _headers={"Authorization": "fake-sdk-auth"},
                is_sandbox=False,
            )
        )
        with self.assertRaises(qq.APIError) as error:
            await qq._request(
                api, qq._route("POST", "/v2/media-images/prepare"), json={}
            )
        self.assertEqual(error.exception.status, 400)
        self.assertEqual(error.exception.code, 40034025)
        self.assertNotIn("sensitive", str(error.exception))
        self.assertEqual(session.request.call_count, 1)
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])

    async def test_private_scope_isolated_by_person_and_bot(self):
        first, second = OfficialEvent(group=""), OfficialEvent(group="", user="other")
        changed_app = OfficialEvent(group="", app="different-app")
        self.assertRegex(qq.private_scope(first), r"^qqdm_[0-9a-f]{32}$")
        self.assertNotEqual(qq.private_scope(first), qq.private_scope(second))
        self.assertNotEqual(qq.private_scope(first), qq.private_scope(changed_app))
        self.assertEqual(qq.private_scope(OfficialEvent()), "")
        self.assertEqual(qq.private_scope(FakeEvent("今日盟友")), "")
        self.assertNotIn("user-openid", qq.private_scope(first))

    async def test_page_permissions_expiry_and_bounds(self):
        with TemporaryDirectory() as temp:
            delivery = qq.QQOfficialDelivery(Path(temp))
            first = OfficialEvent()
            token = delivery.cache_pages(
                first, [[Comp.Plain("a")], [Comp.Plain("b")]], qq.Presentation()
            )
            self.assertEqual(len(delivery.resolve_page(first, token, 2).pages), 2)
            for event, page in (
                (OfficialEvent(user="other"), 1),
                (OfficialEvent(group="other"), 1),
                (first, 0),
                (first, 3),
            ):
                with self.subTest(page=page), self.assertRaises(ValueError):
                    delivery.resolve_page(event, token, page)
            delivery.readings[token].expires = time.monotonic() - 1
            with self.assertRaisesRegex(ValueError, "过期"):
                delivery.resolve_page(first, token, 1)
            self.assertNotIn(token, delivery.readings)

    async def test_page_cache_evicts_oldest_and_prunes_expired(self):
        with TemporaryDirectory() as temp:
            delivery = qq.QQOfficialDelivery(Path(temp))
            tokens = [
                delivery.cache_pages(
                    OfficialEvent(), [[Comp.Plain("a")]], qq.Presentation()
                )
                for _ in range(qq.MAX_PAGE_SESSIONS + 1)
            ]
            self.assertEqual(len(delivery.readings), qq.MAX_PAGE_SESSIONS)
            self.assertNotIn(tokens[0], delivery.readings)
            for reading in delivery.readings.values():
                reading.expires = 0
            delivery.cache_pages(
                OfficialEvent(), [[Comp.Plain("b")]], qq.Presentation()
            )
            self.assertEqual(len(delivery.readings), 1)

    async def test_nodes_keep_every_plain_and_image_without_mention(self):
        node = Comp.Node(
            uin="bot",
            name="Bot",
            content=[Comp.Plain("正文"), Comp.Image.fromFileSystem("/example.png")],
        )
        flattened = qq.flatten([Comp.Nodes(nodes=[node]), Comp.At(qq="user")])
        self.assertEqual(len(flattened), 2)
        self.assertEqual(flattened[0].text, "正文")

    async def test_reading_bound_rejects_excess_without_silent_truncation(self):
        with self.assertRaises(ValueError):
            qq.pages_for([Comp.Plain("字" * (qq.MAX_SESSION_CHARS + 1))])

    async def test_character_buttons_use_catalogue_id_and_real_wiki_title(self):
        commands = [item.command for item in qq.character_actions(ENTRY)]
        self.assertEqual(commands, ["素材 #12", "查看简介 #12", "卡比百科 Waddle Dee"])
        actions = qq.keyboard(
            (
                qq.Action("再抽一次", "今日盟友", True, True),
                *qq.character_actions(ENTRY),
            ),
            "user-openid",
        )
        buttons = [
            item for row in actions["content"]["rows"] for item in row["buttons"]
        ]
        self.assertEqual(
            buttons[0]["action"]["permission"],
            {"type": 0, "specify_user_ids": ["user-openid"]},
        )
        self.assertEqual(buttons[-1]["action"]["permission"], {"type": 2})
        self.assertTrue(
            all(
                item["action"]["type"] == 2 and item["action"]["enter"]
                for item in buttons
            )
        )

    async def test_bot_caption_no_prefix_and_no_draw_button_when_exhausted(self):
        outcome = AllyDrawOutcome(ENTRY, 0, True, False, daily_limit=5)
        view = qq.draw_presentation(
            outcome, "爱丽丝", "瓦豆鲁迪", is_bot=True, limit=5, wiki=False
        )
        self.assertTrue(view.intro.startswith("我今天的盟友是"))
        self.assertNotIn("爱丽丝", view.intro)
        self.assertIn("重复", view.intro)
        self.assertFalse(any(item.command == "Bot今日盟友" for item in view.actions))
        self.assertFalse(any("百科" in item.command for item in view.actions))

    async def test_escape_user_markdown_but_preserves_readable_content(self):
        text = qq.escape(
            "[name](https://example.com)\n# 假标题\n- 假列表\n完整简介 <tag>"
        )
        self.assertIn(r"\[name\]\(https://example.com\)", text)
        self.assertIn(r"\# 假标题", text)
        self.assertIn(r"\- 假列表", text)
        self.assertIn("完整简介 &lt;tag&gt;", text)


class ScopedStore(FakeStore):
    def __init__(self, entry):
        super().__init__(entry)
        self.groups = {}

    def load_group(self, group_id):
        return deepcopy(self.groups.get(group_id, {}))

    def save_group(self, group_id, config):
        self.groups[group_id] = deepcopy(config)


class PluginOfficialFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.plugin = make_plugin(ENTRY)
        self.plugin.store = ScopedStore(ENTRY)
        self.plugin.store.root = Path(self.temp.name)
        self.plugin.config = {
            "draw_cooldown_seconds": 0,
            "qq_official_draw_card": False,
        }
        self.plugin._chain_result_with_media = AsyncMock(return_value=None)

    async def test_private_draw_records_and_counts_do_not_mix(self):
        first, other = OfficialEvent(group=""), OfficialEvent(group="", user="other")
        for event in (first, other):
            self.assertEqual([item async for item in self.plugin.draw_ally(event)], [])
        self.assertEqual(len(self.plugin.store.groups), 2)
        self.assertEqual(sorted(self.plugin.store.draws.values()), [1, 1])
        self.assertEqual(
            set(self.plugin.store.load_group(self.plugin._group_id(first))),
            {first.sender_id},
        )

    async def test_send_failure_keeps_exactly_one_draw_and_saved_collection(self):
        self.plugin._chain_result_with_media = AsyncMock(side_effect=TimeoutError())
        event = OfficialEvent()
        results = [item async for item in self.plugin.draw_ally(event)]
        self.assertEqual(sum(self.plugin.store.draws.values()), 1)
        self.assertEqual(
            len(
                self.plugin.store.load_group(self.plugin._group_id(event))[
                    event.sender_id
                ]["unlocked"]
            ),
            1,
        )
        self.assertIn("结果已保存", results[0])

    async def test_bot_command_and_tool_keep_independent_identity_and_one_count_each(
        self,
    ):
        event = OfficialEvent()
        with patch.object(
            self.plugin, "_bot_draw_tool_result", AsyncMock(return_value="done")
        ):
            self.assertEqual(
                [item async for item in self.plugin.draw_bot_ally(event)], []
            )
            self.assertEqual(await self.plugin.draw_bot_ally_tool(event), "done")
        keys = list(self.plugin.store.draws)
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0][1].startswith("bot_qq_"))
        self.assertEqual(sum(self.plugin.store.draws.values()), 2)
        self.assertNotIn(
            event.sender_id, self.plugin.store.load_group(self.plugin._group_id(event))
        )

    async def test_qq_forced_card_honours_existing_theme_and_disabled_setting(self):
        def render(_data, output):
            Image.new("RGB", (40, 62), "pink").save(output)
            return output

        self.plugin.config = {
            "qq_official_draw_card": True,
            "ally_draw_card_theme": "游戏王卡面随机",
        }
        outcome = AllyDrawOutcome(ENTRY, 2, False, False)
        with patch.object(
            main_module, "render_ally_draw_card", side_effect=render
        ) as renderer:
            chain = await self.plugin._daily_ally_chain(
                outcome, "狐狸", "自定义文本", event=OfficialEvent()
            )
            self.assertTrue(
                self.plugin._qq_draw_view(outcome, "狐狸", chain).replace_caption
            )
            self.assertEqual(renderer.call_count, 1)
        for config in (
            {"qq_official_enabled": False},
            {"qq_official_draw_card": False},
        ):
            self.plugin.config = config
            with patch.object(main_module, "render_ally_draw_card") as renderer:
                chain = await self.plugin._daily_ally_chain(
                    outcome, "狐狸", "自定义文本", event=OfficialEvent()
                )
                renderer.assert_not_called()
                self.assertTrue(chain[0].text.startswith("自定义文本"))

    async def test_custom_caption_and_render_fallback_are_not_swallowed(self):
        outcome = AllyDrawOutcome(ENTRY, 2, False, False)
        card = Comp.Image.fromFileSystem(
            str(self.plugin.store.root / "kirby-ally-card-test.png")
        )
        self.plugin.config = {"ally_draw_output_mode": "卡片＋文字"}
        self.assertFalse(
            self.plugin._qq_draw_view(
                outcome, "狐狸", [Comp.Plain("自定义文本"), card]
            ).replace_caption
        )
        self.plugin.config = {"ally_draw_output_mode": "精美卡片"}
        self.assertFalse(
            self.plugin._qq_draw_view(
                outcome, "狐狸", [Comp.Plain("自定义文本")]
            ).replace_caption
        )

    async def test_page_handler_does_not_change_draw_or_collection(self):
        event = OfficialEvent()
        delivery = self.plugin._qq_official_delivery()
        token = delivery.cache_pages(
            event, [[Comp.Plain("a")], [Comp.Plain("b")]], qq.Presentation()
        )
        event.message_str = f"卡比翻页 {token} 2"
        with patch.object(
            delivery, "send", AsyncMock(return_value=["page-id"])
        ) as send:
            self.assertEqual(
                [item async for item in self.plugin.qq_reading_page(event)], []
            )
        self.assertEqual(send.call_args.kwargs["page"], 2)
        self.assertFalse(self.plugin.store.draws)
        self.assertFalse(self.plugin.store.groups)

    async def test_help_and_exact_material_command_get_character_actions(self):
        event = OfficialEvent("素材 #12")
        self.assertEqual(
            [item async for item in self.plugin.view_ally_asset(event)], []
        )
        view = self.plugin._chain_result_with_media.call_args.kwargs["qq_view"]
        self.assertIn("查看简介 #12", [item.command for item in view.actions])
        self.assertEqual(
            [
                item
                async for item in self.plugin.help_command(OfficialEvent("盟友帮助"))
            ],
            [],
        )
        view = self.plugin._chain_result_with_media.call_args.kwargs["qq_view"]
        self.assertIn("我的图鉴进度", [item.command for item in view.actions])

    async def test_schema_and_metadata_expose_both_official_adapters(self):
        root = Path(main_module.__file__).parent
        schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
        settings = schema["qq_official_settings"]["items"]
        self.assertTrue(settings["qq_official_enabled"]["default"])
        self.assertTrue(settings["qq_official_buttons_enabled"]["default"])
        metadata = (root / "metadata.yaml").read_text(encoding="utf-8")
        self.assertIn("  - qq_official\n", metadata)
        self.assertIn("  - qq_official_webhook\n", metadata)
