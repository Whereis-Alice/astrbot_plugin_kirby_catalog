# Third-Party Notices

## WiKirby catalog profiles

`resources/catalog_profiles.json` contains names, debut works, short quotations,
and introductory text derived from pages on [WiKirby](https://wikirby.com/).
The source material is available under the GNU Free Documentation License 1.3
or any later version.

Each generated record retains:

- the WiKirby page title and URL;
- the source revision ID and timestamp;
- the extracted English text;
- the simplified Chinese translation and terminology-normalized derivative.

The translation removes wiki markup, shortens the material to the page lead,
uses verified Chinese and English names where available, and may add one short
sentence identifying an independently cataloged form or variant. Authors and
revision history can be found through the history page associated with each
record's source URL.

The bundled derivative database is distributed under the same GNU Free
Documentation License 1.3 or later. An unmodified copy of version 1.3 is
included at [`LICENSES/GFDL-1.3.txt`](LICENSES/GFDL-1.3.txt).

WiKirby and its contributors do not endorse this plugin. Kirby and related
names are trademarks or copyrights of their respective owners.

## Online encyclopedia queries

The plugin can query WiKirby, [Kirby Wiki | Fandom](https://kirby.fandom.com/),
and [星のカービィ 真 ボスバトル攻略Wiki](https://seesaawiki.jp/kirby_shinkaku/)
at runtime. Those responses remain subject to the source site's terms and
content license. Fandom and Seesaa Wiki article content, tables, and images are
not bundled in this repository. The Seesaa source is used only for user-initiated
read-only lookup of publicly available Boss Battle guide pages and its English
Corner terminology table.

## 真格 Wiki page-name index

`resources/shinkaku_page_names.json`, `.csv`, and `.md` contain a manually
curated lookup index derived from the public Seesaa Wiki page list. They bundle
page titles, editorial Chinese/English labels, categories, translation-status
metadata, and source URLs only. They do not bundle Seesaa article prose,
tables, guide data, or images. Entries marked `translated` are this project's
editorial translations rather than claims of official Nintendo localization.

## Lucide icons

The catalog management Page bundles Lucide `v1.28.0` in
`pages/catalog-admin/vendor/lucide.min.js`. Lucide is distributed under the
ISC License. Icons derived from the Feather project remain available under the
MIT License. The complete notices are included at
[`LICENSES/LUCIDE-ISC.txt`](LICENSES/LUCIDE-ISC.txt).

## Plugin code

Unless a file says otherwise, the plugin source code is distributed under the
[MIT License](LICENSE).
