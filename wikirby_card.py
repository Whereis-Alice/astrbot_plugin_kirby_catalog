from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

DEFAULT_CARD_TEMPLATE = "梦之泉"
CARD_TEMPLATE_NAMES = ("梦之泉", "卡比粉彩", "瓦豆鲁迪", "星际档案")

CARD_TEMPLATES: dict[str, dict[str, str]] = {
    "梦之泉": {
        "slug": "fountain",
        "label": "梦之泉",
        "eyebrow": "梦之泉档案",
        "canvas": "#d8efee",
        "surface": "#eef8f5",
        "header": "#cce9e7",
        "image_bg": "#d8f2ee",
        "title": "#174b57",
        "text": "#244f58",
        "muted": "#58777d",
        "accent": "#269f99",
        "accent_alt": "#6678cf",
        "accent_warm": "#dfa43f",
        "border": "#9bcac6",
        "band": "#d7ece4",
        "footer": "#c7e2df",
        "chip": "#b7dcd9",
        "chip_text": "#194a53",
        "panel_a": "#e2f4ef",
        "panel_b": "#e7ebfa",
        "panel_c": "#fff0c9",
        "panel_d": "#dceff4",
    },
    "卡比粉彩": {
        "slug": "popstar",
        "label": "卡比粉彩",
        "eyebrow": "波普之星角色档案",
        "canvas": "#f4dce7",
        "surface": "#fceaf1",
        "header": "#f4cada",
        "image_bg": "#f8dce8",
        "title": "#783052",
        "text": "#5b3a4d",
        "muted": "#856176",
        "accent": "#c95787",
        "accent_alt": "#3f9fbd",
        "accent_warm": "#dda33d",
        "border": "#dda9c0",
        "band": "#e8e5f5",
        "footer": "#edcedc",
        "chip": "#e6b6ca",
        "chip_text": "#70304f",
        "panel_a": "#f9e1eb",
        "panel_b": "#ddeef5",
        "panel_c": "#f3e6bd",
        "panel_d": "#e7e1f4",
    },
    "瓦豆鲁迪": {
        "slug": "waddle",
        "label": "瓦豆鲁迪",
        "eyebrow": "瓦豆鲁迪观察笔记",
        "canvas": "#f7eddc",
        "surface": "#fff8ee",
        "header": "#f6d49f",
        "image_bg": "#f8dfb4",
        "title": "#694237",
        "text": "#5d4d45",
        "muted": "#826f65",
        "accent": "#c8674f",
        "accent_alt": "#399486",
        "accent_warm": "#d9a338",
        "border": "#e5c69b",
        "band": "#e8f0e4",
        "footer": "#f3dfbd",
        "chip": "#f0c98f",
        "chip_text": "#694237",
        "panel_a": "#fdf0dc",
        "panel_b": "#e3f1eb",
        "panel_c": "#fae7b5",
        "panel_d": "#e7eff2",
    },
    "星际档案": {
        "slug": "archive",
        "label": "星际档案",
        "eyebrow": "星际参考档案",
        "canvas": "#dce3f2",
        "surface": "#eef1f9",
        "header": "#d1daed",
        "image_bg": "#d9e3f5",
        "title": "#293b61",
        "text": "#354665",
        "muted": "#66728e",
        "accent": "#566bb1",
        "accent_alt": "#318fa6",
        "accent_warm": "#c79a34",
        "border": "#b2bdd7",
        "band": "#e1e7f4",
        "footer": "#d2daeb",
        "chip": "#c2cce3",
        "chip_text": "#293a5d",
        "panel_a": "#e2e7f5",
        "panel_b": "#dceef2",
        "panel_c": "#f2e7c6",
        "panel_d": "#eadfee",
    },
}

WIKIRBY_CARD_TEMPLATE = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=1600, height=600, initial-scale=1" />
  <style>
    * {
      box-sizing: border-box;
      letter-spacing: 0;
    }

    html,
    body {
      width: 1600px;
      margin: 0;
      padding: 0;
      background: {{ theme.canvas }};
    }

    body {
      font-family: "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC",
        Arial, sans-serif;
      color: {{ theme.text }};
      font-weight: 500;
      text-rendering: geometricPrecision;
      -webkit-font-smoothing: antialiased;
    }

    #kirby-card {
      width: 1280px;
      zoom: 1.25;
      overflow: hidden;
      background: {{ theme.surface }};
      border: 1px solid {{ theme.border }};
      border-radius: 4px;
      box-shadow: 0 12px 30px rgba(48, 66, 78, 0.10);
    }

    .color-rail {
      display: grid;
      grid-template-columns: 46% 31% 23%;
      height: 12px;
    }

    .color-rail span:nth-child(1) { background: {{ theme.accent }}; }
    .color-rail span:nth-child(2) { background: {{ theme.accent_alt }}; }
    .color-rail span:nth-child(3) { background: {{ theme.accent_warm }}; }

    .masthead {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 28px;
      align-items: end;
      padding: 34px 44px 30px;
      background: {{ theme.header }};
      border-bottom: 1px solid {{ theme.border }};
    }

    .eyebrow {
      margin: 0 0 10px;
      color: {{ theme.accent }};
      font-size: 17px;
      font-weight: 800;
      line-height: 1.2;
    }

    .title {
      margin: 0;
      color: {{ theme.title }};
      font-size: 44px;
      font-weight: 800;
      line-height: 1.18;
      overflow-wrap: anywhere;
    }

    .subtitle {
      margin-top: 10px;
      color: {{ theme.muted }};
      font-size: 18px;
      line-height: 1.5;
    }

    .page-chip {
      min-width: 112px;
      padding: 12px 15px;
      color: {{ theme.chip_text }};
      background: {{ theme.chip }};
      border: 1px solid {{ theme.border }};
      border-radius: 4px;
      text-align: center;
    }

    .page-chip strong {
      display: block;
      color: {{ theme.accent_alt }};
      font-size: 21px;
      line-height: 1;
    }

    .page-chip span {
      display: block;
      margin-top: 7px;
      color: {{ theme.muted }};
      font-size: 14px;
    }

    .hero {
      display: grid;
      grid-template-columns: 1fr 390px;
      min-height: 330px;
      background: {{ theme.image_bg }};
      border-bottom: 1px solid {{ theme.border }};
    }

    .hero-copy {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 42px 30px 42px 44px;
      background: {{ theme.panel_a }};
    }

    .hero-copy .index {
      color: {{ theme.accent_alt }};
      font-size: 16px;
      font-weight: 800;
    }

    .hero-copy .hero-title {
      margin-top: 12px;
      color: {{ theme.text }};
      font-size: 26px;
      font-weight: 800;
      line-height: 1.25;
    }

    .hero-copy .hero-note {
      margin-top: 14px;
      color: {{ theme.muted }};
      font-size: 19px;
      font-weight: 500;
      line-height: 1.65;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .hero-art {
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px 40px 24px 12px;
      background: {{ theme.image_bg }};
    }

    .hero-art img {
      display: block;
      width: 100%;
      height: 270px;
      object-fit: contain;
    }

    .hero--text {
      display: block;
      min-height: 0;
    }

    .hero--text .hero-copy {
      padding-right: 44px;
    }

    .content {
      padding: 0;
      background: {{ theme.surface }};
    }

    .facts-band {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 18px;
      padding: 24px 44px;
      background: {{ theme.band }};
      border-bottom: 1px solid {{ theme.border }};
    }

    .facts-band .content-block {
      margin: 0;
      padding: 20px 24px 22px;
      border-bottom: 0;
      border-left: 5px solid {{ theme.accent }};
      background: {{ theme.panel_b }};
    }

    .facts-band .content-block:nth-child(2n) {
      border-left-color: {{ theme.accent_warm }};
      background: {{ theme.panel_c }};
    }

    .rich-area {
      padding: 30px 44px 10px;
      background: {{ theme.surface }};
      border-bottom: 1px solid {{ theme.border }};
    }

    .rich-section {
      margin-bottom: 26px;
    }

    .rich-section:last-child {
      margin-bottom: 10px;
    }

    .rich-heading {
      display: grid;
      grid-template-columns: 9px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
      margin-bottom: 15px;
    }

    .rich-heading-mark {
      width: 9px;
      height: 34px;
      background: {{ theme.accent }};
      border-radius: 2px;
    }

    .rich-title {
      color: {{ theme.title }};
      font-size: 23px;
      font-weight: 800;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }

    .rich-context {
      margin-top: 3px;
      color: {{ theme.muted }};
      font-size: 15px;
      font-weight: 600;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .quote-list {
      border: 1px solid {{ theme.border }};
      border-left: 6px solid {{ theme.accent_alt }};
      background: {{ theme.panel_a }};
    }

    .quote-item {
      position: relative;
      min-width: 0;
      padding: 23px 28px 20px 66px;
      border-bottom: 1px solid {{ theme.border }};
      background: {{ theme.panel_a }};
    }

    .quote-item:nth-child(2n) {
      background: {{ theme.panel_b }};
    }

    .quote-item:nth-child(3n) {
      background: {{ theme.panel_c }};
    }

    .quote-item:last-child {
      border-bottom: 0;
    }

    .quote-mark {
      position: absolute;
      top: 12px;
      left: 22px;
      color: {{ theme.accent }};
      font-family: Georgia, "Times New Roman", serif;
      font-size: 54px;
      font-weight: 800;
      line-height: 1;
    }

    .quote-text {
      color: {{ theme.text }};
      font-family: Georgia, "Noto Serif CJK SC", "Songti SC", serif;
      font-size: 20px;
      font-style: italic;
      font-weight: 600;
      line-height: 1.65;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .quote-credit {
      margin-top: 11px;
      color: {{ theme.muted }};
      font-size: 15px;
      font-weight: 700;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }

    .technique-intro {
      margin: -2px 0 16px;
      padding: 14px 18px;
      color: {{ theme.text }};
      background: {{ theme.panel_d }};
      border-left: 5px solid {{ theme.accent_warm }};
      font-size: 16px;
      line-height: 1.6;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .technique-group + .technique-group {
      margin-top: 22px;
    }

    .technique-group-heading {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 9px;
      color: {{ theme.accent_alt }};
      font-size: 16px;
      font-weight: 800;
      line-height: 1.4;
    }

    .technique-group-heading::before {
      content: "";
      width: 24px;
      height: 4px;
      background: {{ theme.accent_warm }};
    }

    .technique-table-wrap {
      overflow: hidden;
      border: 1px solid {{ theme.border }};
      background: {{ theme.panel_a }};
    }

    .technique-table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }

    .technique-table col.move { width: 17%; }
    .technique-table col.controls { width: 19%; }
    .technique-table col.description { width: 48%; }
    .technique-table col.damage { width: 16%; }

    .technique-table th {
      padding: 13px 14px;
      color: {{ theme.title }};
      background: {{ theme.header }};
      border-right: 1px solid {{ theme.border }};
      border-bottom: 2px solid {{ theme.accent }};
      font-size: 16px;
      font-weight: 800;
      line-height: 1.35;
      text-align: center;
    }

    .technique-table th:last-child,
    .technique-table td:last-child {
      border-right: 0;
    }

    .technique-table td {
      padding: 15px 15px;
      color: {{ theme.text }};
      background: {{ theme.panel_a }};
      border-right: 1px solid {{ theme.border }};
      border-bottom: 1px solid {{ theme.border }};
      font-size: 15px;
      font-weight: 500;
      line-height: 1.55;
      vertical-align: top;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .technique-table tr:nth-child(2n) td {
      background: {{ theme.panel_b }};
    }

    .technique-table tr:nth-child(3n) td {
      background: {{ theme.panel_d }};
    }

    .technique-table tbody tr:last-child td {
      border-bottom: 0;
    }

    .technique-move {
      color: {{ theme.title }} !important;
      font-weight: 800 !important;
      text-align: center;
      vertical-align: middle !important;
    }

    .technique-controls {
      color: {{ theme.accent_alt }} !important;
      font-weight: 800 !important;
      text-align: center;
      vertical-align: middle !important;
      white-space: pre-line;
    }

    .technique-damage {
      color: {{ theme.text }} !important;
      font-weight: 700 !important;
      text-align: center;
      vertical-align: middle !important;
      white-space: pre-line;
    }

    .rich-warning {
      margin-top: 10px;
      color: {{ theme.muted }};
      font-size: 14px;
      font-weight: 700;
    }

    .details-columns {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      min-width: 0;
      padding: 24px 44px 8px;
      background: {{ theme.surface }};
      align-items: start;
    }

    .details-columns--flush {
      padding-top: 30px;
    }

    .details-column {
      min-width: 0;
    }

    .content-block {
      display: block;
      min-width: 0;
      margin-bottom: 18px;
      padding: 21px 24px 24px;
      border-bottom: 0;
      border-left: 5px solid {{ theme.accent_alt }};
      background: {{ theme.panel_a }};
    }

    .details-column:nth-child(1) .content-block:nth-child(2n),
    .details-column:nth-child(2) .content-block:nth-child(2n + 1) {
      border-left-color: {{ theme.accent }};
      background: {{ theme.panel_b }};
    }

    .details-column:nth-child(1) .content-block:nth-child(3n),
    .details-column:nth-child(2) .content-block:nth-child(3n) {
      border-left-color: {{ theme.accent_warm }};
      background: {{ theme.panel_c }};
    }

    .details-column:nth-child(1) .content-block:nth-child(4n),
    .details-column:nth-child(2) .content-block:nth-child(4n) {
      border-left-color: {{ theme.accent_alt }};
      background: {{ theme.panel_d }};
    }

    .content-block:last-child {
      border-bottom: 0;
      margin-bottom: 0;
    }

    .block-heading {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      margin-bottom: 11px;
      color: {{ theme.accent }};
      font-size: 18px;
      font-weight: 800;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .block-mark {
      flex: 0 0 9px;
      width: 9px;
      height: 28px;
      margin-top: 1px;
      background: {{ theme.accent_alt }};
      border-radius: 2px;
    }

    .block-body {
      min-width: 0;
      color: {{ theme.text }};
      font-size: 18px;
      font-weight: 500;
      line-height: 1.66;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .fact-list {
      margin: 0;
      border-top: 1px solid {{ theme.border }};
    }

    .fact-row {
      display: grid;
      grid-template-columns: minmax(76px, 0.72fr) minmax(0, 1.45fr);
      gap: 10px;
      padding: 10px 0;
      border-bottom: 1px solid {{ theme.border }};
    }

    .fact-row:last-child {
      border-bottom: 0;
    }

    .fact-label {
      margin: 0;
      color: {{ theme.muted }};
      font-size: 15px;
      font-weight: 800;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .fact-value {
      min-width: 0;
      margin: 0;
      color: {{ theme.text }};
      font-size: 16px;
      font-weight: 500;
      line-height: 1.55;
      overflow-wrap: anywhere;
      word-break: break-word;
    }

    .content-block[data-tone="summary"] .block-heading {
      color: {{ theme.accent_alt }};
    }

    .content-block[data-tone="facts"] .block-mark {
      background: {{ theme.accent_warm }};
    }

    .footer {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 18px;
      align-items: start;
      padding: 22px 44px 26px;
      color: {{ theme.muted }};
      background: {{ theme.footer }};
      border-top: 1px solid {{ theme.border }};
      font-size: 16px;
      line-height: 1.55;
    }

    .footer-label {
      color: {{ theme.accent }};
      font-weight: 800;
    }

    .source {
      min-width: 0;
      overflow-wrap: anywhere;
    }

    #kirby-card.template-popstar .masthead {
      display: block;
      text-align: center;
      border-bottom: 8px solid {{ theme.accent_alt }};
    }

    #kirby-card.template-popstar .page-chip {
      display: inline-block;
      margin-top: 22px;
    }

    #kirby-card.template-popstar .hero {
      grid-template-columns: 360px 1fr;
    }

    #kirby-card.template-popstar .hero-copy {
      order: 2;
      padding-left: 18px;
      padding-right: 44px;
    }

    #kirby-card.template-popstar .hero-art {
      order: 1;
      padding-left: 44px;
      padding-right: 12px;
    }

    #kirby-card.template-popstar .facts-band {
      border-bottom: 8px solid {{ theme.accent_warm }};
    }

    #kirby-card.template-fountain .masthead {
      border-left: 18px solid {{ theme.accent }};
      padding-left: 26px;
    }

    #kirby-card.template-fountain .facts-band {
      border-top: 8px solid {{ theme.accent_warm }};
    }

    #kirby-card.template-waddle {
      border-top: 12px solid {{ theme.accent }};
    }

    #kirby-card.template-waddle .color-rail {
      display: none;
    }

    #kirby-card.template-waddle .masthead {
      border-left: 18px solid {{ theme.accent_alt }};
    }

    #kirby-card.template-waddle .hero {
      grid-template-columns: 380px 1fr;
    }

    #kirby-card.template-waddle .hero-copy {
      order: 2;
      padding-left: 18px;
      padding-right: 44px;
    }

    #kirby-card.template-waddle .hero-art {
      order: 1;
      padding-left: 44px;
      padding-right: 12px;
    }

    #kirby-card.template-archive {
      border-radius: 0;
      box-shadow: none;
      border-width: 2px;
    }

    #kirby-card.template-archive .eyebrow,
    #kirby-card.template-archive .subtitle {
      color: {{ theme.muted }};
    }

    #kirby-card.template-archive .page-chip {
      color: {{ theme.chip_text }};
      background: {{ theme.chip }};
      border-color: {{ theme.border }};
    }

    #kirby-card.template-archive .page-chip span {
      color: {{ theme.muted }};
    }

    #kirby-card.template-archive .masthead {
      border-bottom: 5px solid {{ theme.accent }};
    }

    #kirby-card.template-archive .facts-band {
      border-top: 1px dashed {{ theme.accent }};
      border-bottom: 1px dashed {{ theme.accent }};
    }

    #kirby-card.template-archive .block-heading,
    #kirby-card.template-archive .footer-label {
      font-family: "Noto Sans Mono CJK SC", "Microsoft YaHei", monospace;
    }

    @media (max-width: 860px) {
      .masthead {
        padding-left: 34px;
        padding-right: 34px;
      }

      .hero {
        grid-template-columns: 1fr 310px;
      }

      .hero-copy {
        padding-left: 34px;
      }

      .hero-art {
        padding-right: 28px;
      }

      .facts-band,
      .details-columns {
        padding-left: 34px;
        padding-right: 34px;
      }

      .facts-band,
      .details-columns {
        grid-template-columns: 1fr;
      }

      .facts-band {
        gap: 26px;
      }

      .details-columns {
        gap: 28px;
      }

      .footer {
        padding-left: 34px;
        padding-right: 34px;
      }
    }

    @media (max-width: 640px) {
      .masthead {
        grid-template-columns: 1fr;
        gap: 16px;
        align-items: start;
        padding: 28px 28px 24px;
      }

      .title {
        font-size: 34px;
      }

      .subtitle {
        font-size: 16px;
      }

      .page-chip {
        justify-self: start;
        min-width: 0;
      }

      .hero,
      #kirby-card.template-popstar .hero,
      #kirby-card.template-waddle .hero {
        grid-template-columns: 1fr;
      }

      .hero-copy,
      #kirby-card.template-popstar .hero-copy,
      #kirby-card.template-waddle .hero-copy {
        order: 1;
        padding: 30px 28px 20px;
      }

      .hero--text .hero-copy {
        padding-right: 28px;
      }

      .hero-art,
      #kirby-card.template-popstar .hero-art,
      #kirby-card.template-waddle .hero-art {
        order: 2;
        padding: 0 28px 28px;
      }

      .hero-art img {
        height: 220px;
      }

      .facts-band,
      .details-columns {
        padding-left: 28px;
        padding-right: 28px;
      }

      .facts-band {
        gap: 24px;
        padding-top: 26px;
      }

      .block-heading {
        font-size: 17px;
      }

      .block-body {
        font-size: 16px;
      }

      .fact-row {
        grid-template-columns: minmax(70px, 0.72fr) minmax(0, 1.45fr);
      }

      .footer {
        grid-template-columns: 1fr;
        gap: 6px;
        padding: 20px 28px 24px;
      }
    }
  </style>
</head>
<body>
  <main id="kirby-card" class="template-{{ theme.slug }}">
    <div class="color-rail"><span></span><span></span><span></span></div>
    <header class="masthead">
      <div>
        <div class="eyebrow">{{ theme.eyebrow | e }}</div>
        <h1 class="title">{{ title | e }}</h1>
        <div class="subtitle">{{ wiki_name | default('WiKirby', true) | e }} 百科阅读卡片 · {{ theme.label | e }}</div>
      </div>
      <div class="page-chip">
        <strong>百科档案</strong>
        <span>{{ reference_label | default('WIKIRBY REFERENCE', true) | e }}</span>
      </div>
    </header>

    <section class="hero{% if not image_data_uri %} hero--text{% endif %}">
      <div class="hero-copy">
        <div class="index">页面简介</div>
        <div class="hero-title">简介</div>
        <div class="hero-note">{{ summary | e }}</div>
      </div>
      {% if image_data_uri %}
      <div class="hero-art">
        <img src="{{ image_data_uri }}" alt="{{ title | e }}" />
      </div>
      {% endif %}
    </section>

    <div class="content">
      {% if left_blocks %}
      <section class="facts-band">
        {% for block in left_blocks %}
        <section class="content-block" data-tone="{{ block.tone }}">
          <div class="block-heading">
            <span class="block-mark"></span>
            <span>{{ block.title | e }}</span>
          </div>
          {% if block.fact_items %}
          <dl class="fact-list">
            {% for item in block.fact_items %}
            <div class="fact-row">
              <dt class="fact-label">{{ item.label | e }}</dt>
              <dd class="fact-value">{{ item.value | e }}</dd>
            </div>
            {% endfor %}
          </dl>
          {% else %}
          <div class="block-body">{{ block.body | e }}</div>
          {% endif %}
        </section>
        {% endfor %}
      </section>
      {% endif %}
      {% if rich_sections %}
      <section class="rich-area">
        {% for section in rich_sections %}
        <section class="rich-section rich-section--{{ section.kind }}">
          <div class="rich-heading">
            <span class="rich-heading-mark"></span>
            <div>
              <div class="rich-title">{{ section.display_title | e }}</div>
              {% if section.context %}
              <div class="rich-context">{{ section.context | e }}</div>
              {% endif %}
            </div>
          </div>
          {% if section.kind == 'quotes' %}
          <div class="quote-list">
            {% for quote in section.quotes %}
            <article class="quote-item">
              <div class="quote-mark">“</div>
              <div class="quote-text">{{ quote.text | e }}</div>
              {% if quote.attribution or quote.source %}
              <div class="quote-credit">
                — {% if quote.attribution %}{{ quote.attribution | e }}{% endif %}{% if quote.attribution and quote.source %} · {% endif %}{% if quote.source %}{{ quote.source | e }}{% endif %}
              </div>
              {% endif %}
            </article>
            {% endfor %}
          </div>
          {% elif section.kind == 'techniques' %}
            {% if section.intro %}
            <div class="technique-intro">{{ section.intro | e }}</div>
            {% endif %}
            {% for group in section.groups %}
            <section class="technique-group">
              {% if group.label %}
              <div class="technique-group-heading">{{ group.label | e }}</div>
              {% endif %}
              <div class="technique-table-wrap">
                <table class="technique-table">
                  <colgroup>
                    <col class="move" />
                    <col class="controls" />
                    <col class="description" />
                    <col class="damage" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>招式</th>
                      <th>操作</th>
                      <th>说明</th>
                      <th>伤害</th>
                    </tr>
                  </thead>
                  <tbody>
                    {% for row in group.rows %}
                    <tr>
                      <td class="technique-move">{{ row.move | e }}</td>
                      <td class="technique-controls">{{ row.controls | e }}</td>
                      <td>{{ row.description | e }}</td>
                      <td class="technique-damage">{{ row.damage | e }}</td>
                    </tr>
                    {% endfor %}
                  </tbody>
                </table>
              </div>
            </section>
            {% endfor %}
          {% endif %}
          {% if section.incomplete %}
          <div class="rich-warning">该栏目有部分内容未能完整解析，请打开来源页面核对。</div>
          {% endif %}
        </section>
        {% endfor %}
      </section>
      {% endif %}
      {% if right_block_count %}
      <section class="details-columns{% if not left_blocks %} details-columns--flush{% endif %}">
        {% for column in right_columns %}
        <div class="details-column">
          {% for block in column %}
          <section class="content-block" data-tone="{{ block.tone }}">
            <div class="block-heading">
              <span class="block-mark"></span>
              <span>{{ block.title | e }}</span>
            </div>
            <div class="block-body">{{ block.body | e }}</div>
          </section>
          {% endfor %}
        </div>
        {% endfor %}
      </section>
      {% endif %}
    </div>

    <footer class="footer">
      <div class="footer-label">来源</div>
      <div class="source">{{ source | e }}</div>
    </footer>
  </main>
</body>
</html>
"""

_HEADING_RE = re.compile(r"^(.{1,48}?)[：:]$")
_SENTENCE_BREAK_RE = re.compile(r"(?<=[。！？!?；;])\s*")
_LINE_UNITS = 54


def resolve_card_template(value: Any) -> dict[str, str]:
    name = str(value or DEFAULT_CARD_TEMPLATE).strip()
    aliases = {
        "fountain": "梦之泉",
        "dream": "梦之泉",
        "popstar": "卡比粉彩",
        "pink": "卡比粉彩",
        "waddle": "瓦豆鲁迪",
        "archive": "星际档案",
    }
    name = aliases.get(name.casefold(), name)
    return dict(CARD_TEMPLATES.get(name, CARD_TEMPLATES[DEFAULT_CARD_TEMPLATE]))


def estimate_text_lines(text: str) -> int:
    lines = 0
    for raw_line in (text or "").splitlines() or [""]:
        units = sum(_display_units(char) for char in raw_line)
        lines += max(1, math.ceil(units / _LINE_UNITS))
    return lines


def build_card_layout(
    summary: str,
    detail_text: str,
    rich_sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = summary.strip() or "该页面暂时没有可显示的正文摘要。"
    detail_blocks = _detail_blocks(detail_text)
    prepared_rich_sections = _prepare_rich_sections(rich_sections or [])
    left_blocks: list[dict[str, Any]] = []
    right_blocks: list[dict[str, Any]] = []

    for block in detail_blocks:
        if block["tone"] == "facts" or block["title"] == "其他语言名称":
            left_blocks.extend(
                _with_fact_items(part) for part in _split_for_column(block)
            )
        else:
            right_blocks.extend(_split_for_column(block, max_lines=92))

    if not left_blocks and not right_blocks and not prepared_rich_sections:
        left_blocks.append(
            _with_fact_items(
                {
                    "title": "页面资料",
                    "body": "暂无额外资料。",
                    "tone": "facts",
                }
            )
        )

    columns: list[list[dict[str, str]]] = [[], []]
    column_lines = [0, 0]
    for block in right_blocks:
        target = 0 if column_lines[0] <= column_lines[1] else 1
        columns[target].append(block)
        column_lines[target] += estimate_text_lines(block["body"]) + 4

    return {
        "summary": summary,
        "left_blocks": left_blocks,
        "right_columns": columns,
        "right_block_count": sum(len(column) for column in columns),
        "rich_sections": prepared_rich_sections,
        "rich_section_count": len(prepared_rich_sections),
    }


def _prepare_rich_sections(
    rich_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for section in rich_sections:
        if not isinstance(section, dict):
            continue
        kind = str(section.get("kind", "") or "").strip()
        context = str(section.get("context", "") or "").strip()
        incomplete = bool(int(section.get("omitted_count", 0) or 0))
        if kind == "quotes":
            quotes: list[dict[str, str]] = []
            for quote in section.get("quotes", []) or []:
                if not isinstance(quote, dict):
                    continue
                text = str(quote.get("text", "") or "").strip()
                if not text:
                    continue
                quotes.append(
                    {
                        "text": text,
                        "attribution": str(
                            quote.get("attribution", "") or ""
                        ).strip(),
                        "source": str(quote.get("source", "") or "").strip(),
                    }
                )
            if not quotes:
                continue
            prepared.append(
                {
                    "kind": "quotes",
                    "display_title": "相关语录",
                    "context": context,
                    "quotes": quotes,
                    "incomplete": incomplete,
                }
            )
            continue
        if kind != "techniques":
            continue
        groups: list[dict[str, Any]] = []
        for group in section.get("groups", []) or []:
            if not isinstance(group, dict):
                continue
            rows: list[dict[str, str]] = []
            for row in group.get("rows", []) or []:
                if not isinstance(row, dict):
                    continue
                move = str(row.get("move", "") or "").strip()
                description = str(row.get("description", "") or "").strip()
                if not move and not description:
                    continue
                rows.append(
                    {
                        "move": move or "未命名招式",
                        "controls": str(row.get("controls", "") or "").strip()
                        or "—",
                        "description": description or "暂无说明。",
                        "damage": str(row.get("damage", "") or "").strip()
                        or "—",
                    }
                )
            if rows:
                groups.append(
                    {
                        "label": str(group.get("label", "") or "").strip(),
                        "rows": rows,
                    }
                )
        if groups:
            prepared.append(
                {
                    "kind": "techniques",
                    "display_title": "招式与操作",
                    "context": context,
                    "intro": str(section.get("intro", "") or "").strip(),
                    "groups": groups,
                    "incomplete": incomplete,
                }
            )
    return prepared


def _split_for_column(
    block: dict[str, str], max_lines: int = 76
) -> list[dict[str, str]]:
    chunks = _chunk_body(block["body"], max_lines)
    result: list[dict[str, str]] = []
    for index, chunk in enumerate(chunks):
        title = block["title"] if index == 0 else f"{block['title']}（续）"
        result.append({**block, "title": title, "body": chunk})
    return result


def _with_fact_items(block: dict[str, str]) -> dict[str, Any]:
    return {**block, "fact_items": _key_value_items(block["body"])}


def _key_value_items(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    source_lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in source_lines:
        line = line.lstrip("•*- ").strip()
        match = re.fullmatch(r"([^：:]{1,24})[：:]\s*(.+)", line)
        if not match:
            return []
        items.append({"label": match.group(1).strip(), "value": match.group(2).strip()})
    return items if len(items) >= 2 else []


def _detail_blocks(detail_text: str) -> list[dict[str, str]]:
    if not detail_text.strip():
        return []
    blocks: list[dict[str, str]] = []
    title = "资料速览"
    lines: list[str] = []

    def flush() -> None:
        nonlocal lines
        body = "\n".join(line for line in lines if line).strip()
        if body:
            tone = "facts" if title == "资料速览" else "section"
            blocks.append({"title": title, "body": body, "tone": tone})
        lines = []

    for raw_line in detail_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = _HEADING_RE.fullmatch(line)
        if heading:
            flush()
            title = heading.group(1).strip()
            continue
        lines.append(line)
    flush()
    return blocks


def _chunk_body(text: str, max_lines: int) -> list[str]:
    segments = _text_segments(text)
    chunks: list[str] = []
    current: list[str] = []

    for segment in segments:
        for part in _split_oversized_segment(segment, max_lines):
            candidate = "\n".join([*current, part])
            if current and estimate_text_lines(candidate) > max_lines:
                chunks.append("\n".join(current).strip())
                current = [part]
            else:
                current.append(part)
    if current:
        chunks.append("\n".join(current).strip())
    return chunks or [""]


def _text_segments(text: str) -> list[str]:
    segments: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("•", "-", "*")):
            segments.append(line)
            continue
        sentences = [part.strip() for part in _SENTENCE_BREAK_RE.split(line)]
        segments.extend(part for part in sentences if part)
    return segments


def _split_oversized_segment(segment: str, max_lines: int) -> list[str]:
    max_units = max_lines * _LINE_UNITS
    if _text_units(segment) <= max_units:
        return [segment]

    parts: list[str] = []
    remaining = segment
    while remaining:
        units = 0
        cut = 0
        for index, char in enumerate(remaining, start=1):
            next_units = units + _display_units(char)
            if next_units > max_units:
                break
            units = next_units
            cut = index
        if cut >= len(remaining):
            parts.append(remaining.strip())
            break
        preferred = max(
            remaining.rfind(" ", int(cut * 0.6), cut),
            remaining.rfind("，", int(cut * 0.6), cut),
            remaining.rfind(",", int(cut * 0.6), cut),
        )
        if preferred > 0:
            cut = preferred + 1
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [part for part in parts if part]


def _text_units(text: str) -> int:
    return sum(_display_units(char) for char in text)


def _display_units(char: str) -> int:
    if char == "\t":
        return 4
    return 2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
