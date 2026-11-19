#!/usr/bin/env python3
"""Cross-browser check of the on-device smartfan app.

    python tools/xbrowser_check.py [--out DIR] [--headed]

Serves the packed www/ (falling back to app/build) against a FakeController,
the same local preview used for the Help screenshots. It does not talk to a
live fan.

For each of Chromium, Firefox and WebKit, at 390x844, 820x1180, 1280x800 and
2560x1440, it visits Status, Readings, Thresholds, Charts, Health and Help
(Help is a sheet, not a sixth tab), opens a Help topic, follows the Readings
odor Help link into that topic, switches the chart ranges (1 h / 6 h / 24 h /
7 d), and on the phone widths tries the bottom sheet drag-to-dismiss.

WebKit at 390 uses Playwright's iPhone 14 device profile (touch, mobile,
user-agent) with the viewport forced to 390x844 so the three engines share
the same CSS pixel size. Device scale factor is 2 at 390 and 820, 1 at the
desktop widths, so contact sheets compare at the same pixel density.

Writes:

    <out>/<engine>-<width>-<tab>.png
    <out>/<engine>-390-charts-sheet.png   (detail sheet open, 7 d)
    <out>/<engine>-390-help-jump.png      (after the Readings "?" jump)
    <out>/sheet-readings.png
    <out>/sheet-charts.png
    <out>/sheet-help.png
    <out>/report.json
    <out>/console.txt

Needs Playwright browsers (chromium, firefox, webkit) and, for the contact
sheets, Pillow. Linux WebKit is Playwright's port, not Safari on iOS or
macOS; treat WebKit-only paint differences as a hint, not a verdict on
Safari.

This script only reports. It does not change the app.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import httpd
import store
import webapi
from telemetry import Snapshot
from test_httpd import Cfg, FakeController, Pump

DEFAULT_OUT = "/workspace/smartfan-help/xbrowser"
WWW = os.path.join(ROOT, "www")
BUILD = os.path.join(ROOT, "app", "build")
API_PORT_DEFAULT = 8779
APP_PORT_DEFAULT = 8780

TABS = [
    ("status", "Status"),
    ("readings", "Readings"),
    ("thresholds", "Thresholds"),
    ("charts", "Charts"),
    ("health", "Health"),
    ("help", "Help"),
]

VIEWPORTS = [
    (390, 844, "390"),
    (820, 1180, "820"),
    (1280, 800, "1280"),
    (2560, 1440, "2560"),
]

ENGINES = ("chromium", "firefox", "webkit")

# Probe the page for overflow, missing fonts, and CSS features that vary
# across engines. Run after the tab has settled.
PROBE_JS = r"""
() => {
  const issues = [];
  const clipped = [];
  const vx = window.innerWidth;
  const vy = window.innerHeight;
  if (document.documentElement.scrollWidth > vx + 2) {
    issues.push({
      kind: "overflow-x",
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: vx
    });
  }
  const main = document.querySelector(".main-col") || document.querySelector(".stage");
  if (main && main.scrollWidth > main.clientWidth + 2) {
    issues.push({
      kind: "overflow-x-scroller",
      scrollWidth: main.scrollWidth,
      clientWidth: main.clientWidth,
      className: main.className
    });
  }

  const skip = (el) => {
    const cs = getComputedStyle(el);
    // Intentional ellipsis (nav labels) is not a bug.
    if (cs.textOverflow === "ellipsis" && cs.overflowX !== "visible") return true;
    return false;
  };
  for (const el of document.querySelectorAll(
    "h1,h2,h3,h4,p,li,button,label,span.sub,span.k,span.v,span.fact-v,span.label,.blurb,.learned,.tune-hint,.help p,.help li"
  )) {
    if (skip(el)) continue;
    // Firefox reports clientWidth 0 on the uppercase .k labels even
    // when the text paints fully, and often a few px of scrollWidth
    // overflow from letter-spacing. Skip empty boxes and require 8px.
    if (el.clientWidth < 8) continue;
    if (el.scrollWidth > el.clientWidth + 8) {
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none") continue;
      const t = (el.innerText || "").trim().replace(/\s+/g, " ").slice(0, 100);
      if (!t) continue;
      clipped.push({
        text: t,
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
        tag: el.tagName,
        className: String(el.className || "").slice(0, 80)
      });
    }
  }

  const hiddenIcons = [];
  for (const svg of document.querySelectorAll("nav svg, .ico svg, .help-jump svg, header svg")) {
    // Skip icons that are meant to be hidden (a second spin glyph, a
    // header button the nav already shows, display:none at this width).
    let el = svg;
    let hidden = false;
    while (el && el !== document.body) {
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden") { hidden = true; break; }
      el = el.parentElement;
    }
    if (hidden) continue;
    const r = svg.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) {
      hiddenIcons.push({
        width: r.width,
        height: r.height,
        className: String(svg.getAttribute("class") || svg.parentElement?.className || "").slice(0, 80)
      });
    }
  }

  const header = document.querySelector("header");
  const hs = header ? getComputedStyle(header) : null;
  const nav = document.querySelector("nav");
  const ns = nav ? getComputedStyle(nav) : null;
  const stage = document.querySelector(".stage");
  const ss = stage ? getComputedStyle(stage) : null;

  const supports = {
    "backdrop-filter": CSS.supports("backdrop-filter", "blur(16px)")
      || CSS.supports("-webkit-backdrop-filter", "blur(16px)"),
    "color-mix": CSS.supports("background", "color-mix(in srgb, red 50%, blue)"),
    dvh: CSS.supports("height", "100dvh"),
    ":has()": CSS.supports("selector(:has(*))"),
    "container-type": CSS.supports("container-type", "inline-size")
  };

  const axis = Array.from(document.querySelectorAll(".chart-card text, .chart-card .tick, svg text"))
    .map((el) => (el.textContent || "").trim())
    .filter(Boolean)
    .slice(0, 40);

  return {
    innerWidth: vx,
    innerHeight: vy,
    issues,
    clipped: clipped.slice(0, 25),
    hiddenIcons,
    fonts: {
      status: document.fonts.status,
      inter: document.fonts.check("16px Inter"),
      mono: document.fonts.check('16px "JetBrains Mono"')
    },
    supports,
    header: hs && {
      position: hs.position,
      top: hs.top,
      backdropFilter: hs.backdropFilter || hs.webkitBackdropFilter,
      backgroundColor: hs.backgroundColor
    },
    nav: ns && {
      position: ns.position,
      bottom: ns.bottom,
      display: ns.display
    },
    stage: ss && {
      containerType: ss.containerType,
      containerName: ss.containerName
    },
    axis
  };
}
"""


class _Det:
    def __init__(self, raw, base):
        self.raw = raw
        self.base = base


class _Voc:
    ok = True


def seed_history(hist, now, days=7):
    """A week of 5-minute buckets so every chart range has something to draw."""
    clock = [now - days * 86400]
    store._epoch = lambda: clock[0]
    while clock[0] < now:
        into_day = clock[0] % 86400
        showering = 3600 <= into_day <= 5400
        odor_event = 7200 <= into_day <= 7800
        rh = 88.0 if showering else 61.0
        voc_base = 30650
        voc = 30100 if (showering or odor_event) else 30640
        nox_base = 19800
        nox = 19810
        running = showering or odor_event
        hist.note({
            "rh": rh, "base": 60.0, "exc": rh - 60.0, "temp": 21.5,
            "dp": 14.0, "light_pc": 40.0 if showering else 6.0,
            "rpm": 4100 if running else 0, "fan_on": running,
            "light_on": showering,
            "state": "RUN" if running else "IDLE",
            "voc": voc, "voc_base": voc_base, "voc_exc": voc_base - voc,
            "nox": nox, "nox_base": nox_base, "nox_exc": nox_base - nox,
        })
        clock[0] += Cfg.HISTORY_INTERVAL_S
    store._epoch = lambda: now


def arm_odor(controller):
    controller.voc = _Voc()
    controller.vocdet = _Det(30620, 30650)
    controller.excess_voc = 30
    controller.noxdet = _Det(19810, 19800)
    controller.excess_nox = -10
    controller.st._vals.update({
        "VOC_RISE_ON": 140,
        "VOC_FALL_OFF": 70,
        "VOC_NOX_VETO": 300,
        "LIGHT_STEP_ON": 10000,
        "LIGHT_RISE_ON": 8000,
        "LIGHT_RISE_OFF": 4000,
        "LIGHT_ONLY_MAX_S": 600,
        "MIN_RUN_S": 300,
        "MAX_RUN_S": 2700,
        "POST_LIGHT_S": 120,
        "BOOST_S": 900,
    })


def start_preview(api_port, app_port, work):
    has_www = os.path.isdir(WWW) and (
        os.path.exists(os.path.join(WWW, "index.html.gz"))
        or os.path.exists(os.path.join(WWW, "index.html"))
    )
    root = WWW if has_www else BUILD
    if not os.path.isdir(root):
        sys.exit("no www/ or app/build; run: cd app && npm run build && python3 tools/build_www.py --no-build")

    os.makedirs(work, exist_ok=True)
    controller = FakeController()
    arm_odor(controller)
    snap = Snapshot(Cfg, log=lambda *a: None)
    hist = store.History(Cfg, log=lambda *a: None, where=work)
    hist.clear()
    now = int(time.time()) // Cfg.HISTORY_INTERVAL_S * Cfg.HISTORY_INTERVAL_S
    seed_history(hist, now)
    api = webapi.Api(controller, Cfg, snap, hist)
    server = httpd.Server(handler=api.handle, root=None, port=api_port, log=lambda *a: None)
    if not server.start():
        sys.exit("could not bind api on %s" % api_port)
    pump = Pump(server)
    pump.start()
    files_api = webapi.Api(controller, Cfg, snap, hist)
    files = httpd.Server(handler=files_api.handle, root=root, port=app_port, log=lambda *a: None)
    if not files.start():
        pump.stop()
        server.stop()
        sys.exit("could not bind app on %s" % app_port)
    files_pump = Pump(files)
    files_pump.start()
    return {
        "root": root,
        "server": server,
        "pump": pump,
        "files": files,
        "files_pump": files_pump,
    }


def stop_preview(preview):
    preview["pump"].stop()
    preview["server"].stop()
    preview["files_pump"].stop()
    preview["files"].stop()


def context_kwargs(pw, engine, width, height):
    """Viewport plus the iPhone 14 profile extras for WebKit at 390."""
    kwargs = {
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 2 if width <= 820 else 1,
        "color_scheme": "dark",
        "timezone_id": "America/New_York",
        "reduced_motion": "reduce",
    }
    if width <= 820:
        kwargs["has_touch"] = True
        kwargs["is_mobile"] = True
    if engine == "webkit" and width == 390:
        device = pw.devices["iPhone 14"]
        kwargs["user_agent"] = device["user_agent"]
        kwargs["has_touch"] = True
        kwargs["is_mobile"] = device.get("is_mobile", True)
        # Keep dpr at 2 (not the profile's 3) so phone contact sheets
        # compare the three engines at the same pixel density.
    return kwargs


def attach_logging(page, sink, engine, width):
    def on_console(msg):
        try:
            text = msg.text
        except Exception:
            text = "<unreadable>"
        sink.append({
            "engine": engine,
            "width": width,
            "type": msg.type,
            "text": text,
            "when": "console",
        })

    def on_pageerror(err):
        sink.append({
            "engine": engine,
            "width": width,
            "type": "pageerror",
            "text": str(err),
            "when": "pageerror",
        })

    def on_requestfailed(req):
        fail = req.failure
        sink.append({
            "engine": engine,
            "width": width,
            "type": "requestfailed",
            "text": "%s %s" % (req.url, fail.get("errorText") if isinstance(fail, dict) else fail),
            "when": "requestfailed",
        })

    def on_response(resp):
        if resp.status < 400:
            return
        sink.append({
            "engine": engine,
            "width": width,
            "type": "http-%d" % resp.status,
            "text": "%s %s" % (resp.status, resp.url),
            "when": "response",
        })

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("requestfailed", on_requestfailed)
    page.on("response", on_response)


def dismiss_dialogs(page):
    """Close any open sheet so the next nav click is not intercepted."""
    for _ in range(3):
        dialog = page.locator("[role='dialog']")
        if dialog.count() == 0 or not dialog.first.is_visible():
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        if dialog.count() == 0 or not dialog.first.is_visible():
            return
        closer = page.get_by_role("button", name="Close chart")
        if closer.count() and closer.first.is_visible():
            closer.first.click()
            page.wait_for_timeout(200)
            continue
        closer = page.get_by_role("button", name="Close help")
        if closer.count() and closer.first.is_visible():
            closer.first.click()
            page.wait_for_timeout(200)
            continue
        scrim = page.locator("button.scrim")
        if scrim.count() and scrim.first.is_visible():
            scrim.first.click(force=True)
            page.wait_for_timeout(200)


def click_tab(page, label):
    dismiss_dialogs(page)
    page.get_by_role("button", name=label, exact=True).first.click()
    page.wait_for_timeout(350)


def open_chart_detail(page, title="Humidity against its baseline"):
    btn = page.get_by_role("button", name=title + " — open detail")
    btn.first.click()
    page.get_by_role("dialog").wait_for(state="visible", timeout=10000)
    page.wait_for_timeout(400)


def switch_ranges(page, findings, engine, width):
    group = page.get_by_role("group", name="Time range")
    group.wait_for(state="visible", timeout=10000)
    for label in ("1 h", "6 h", "24 h", "7 d"):
        group.get_by_role("button", name=label, exact=True).click()
        page.wait_for_timeout(250)
        # The selected range should keep the aria-pressed/on class and stay visible.
        btn = group.get_by_role("button", name=label, exact=True)
        if not btn.is_visible():
            findings.append({
                "severity": "high",
                "engine": engine,
                "width": width,
                "tab": "charts",
                "issue": "Time range button %r is not visible after click." % label,
            })
    return True


def drag_sheet_down(page, findings, engine, width, tab):
    """Drag the open bottom sheet down far enough to dismiss it.

    Settings and the chart detail sheet both carry a visible grabber
    (`data-sheet-handle`) below 560px. A downward PointerEvent drag on
    that handle should dismiss the sheet. If no handle is visible, a
    drag from the sheet header is tried as a fallback and noted.
    """
    dialog = page.get_by_role("dialog")
    handle = page.locator("[data-sheet-handle]").first
    used = "grabber"
    target = None
    if handle.count():
        display = handle.evaluate("el => getComputedStyle(el).display")
        box = handle.bounding_box()
        if display == "none":
            findings.append({
                "severity": "info",
                "engine": engine,
                "width": width,
                "tab": tab,
                "issue": "Sheet grabber is display:none at this width, so drag is supposed to be inert.",
            })
            return
        if box:
            target = box
    if target is None:
        # Chart detail: no grabber. Start on the title, left of the Close button.
        head = page.locator("[role='dialog'] h3").first
        if head.count():
            box = head.bounding_box()
            if box:
                target = box
                used = "sheet header"
                findings.append({
                    "severity": "info",
                    "engine": engine,
                    "width": width,
                    "tab": tab,
                    "issue": "Chart detail sheet has no data-sheet-handle; tried a drag from the sheet header instead.",
                })
    if not target:
        findings.append({
            "severity": "medium",
            "engine": engine,
            "width": width,
            "tab": tab,
            "issue": "Could not find a drag start point on the open sheet.",
        })
        return
    # Playwright's page.mouse, in Firefox with has_touch=True, does not
    # reliably synthesize PointerEvents (the API this gesture actually
    # listens for). Dispatching them on the handle is the same path a
    # real finger or mouse uses, and it works in all three engines.
    start = page.locator("[data-sheet-handle]").first
    if start.count() == 0 or start.evaluate("el => getComputedStyle(el).display") == "none":
        start = page.locator("[role='dialog'] h3").first
    start.evaluate(
        """(el, dy) => {
          const r = el.getBoundingClientRect();
          const x = r.left + Math.min(24, r.width / 2);
          const y = r.top + r.height / 2;
          const fire = (type, y2, buttons) => {
            el.dispatchEvent(new PointerEvent(type, {
              bubbles: true, cancelable: true, composed: true,
              pointerId: 1, pointerType: 'mouse', isPrimary: true,
              clientX: x, clientY: y2, buttons, button: 0, view: window
            }));
          };
          fire('pointerdown', y, 1);
          for (let i = 1; i <= 12; i++) fire('pointermove', y + dy * i / 12, 1);
          fire('pointerup', y + dy, 0);
        }""",
        140,
    )
    page.wait_for_timeout(400)
    still = dialog.count() > 0 and dialog.first.is_visible()
    if still:
        findings.append({
            "severity": "high",
            "engine": engine,
            "width": width,
            "tab": tab,
            "issue": "Bottom sheet did not dismiss after a 140px downward drag on the %s." % used,
        })


def screenshot(page, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    page.screenshot(path=path, full_page=False)
    return path


def make_contact_sheets(out):
    """One row of the three engines at 390, for Readings, Charts and Help."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("skipping contact sheets (Pillow is not installed)")
        return []

    tabs = ("readings", "charts", "help")
    paths = []
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22
        )
        small = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16
        )
    except Exception:
        font = ImageFont.load_default()
        small = font

    label_h = 36
    gap = 12
    pad = 16
    for tab in tabs:
        images = []
        for engine in ENGINES:
            p = os.path.join(out, "%s-390-%s.png" % (engine, tab))
            if not os.path.isfile(p):
                continue
            im = Image.open(p).convert("RGB")
            images.append((engine, im))
        if len(images) < 2:
            continue
        # Same display height: scale each to the shortest image's height.
        target_h = min(im.height for _, im in images)
        scaled = []
        for engine, im in images:
            if im.height != target_h:
                w = int(im.width * (target_h / im.height))
                im = im.resize((w, target_h), Image.Resampling.LANCZOS)
            scaled.append((engine, im))
        total_w = pad * 2 + gap * (len(scaled) - 1) + sum(im.width for _, im in scaled)
        total_h = pad * 2 + label_h + target_h
        sheet = Image.new("RGB", (total_w, total_h), (12, 15, 22))
        draw = ImageDraw.Draw(sheet)
        draw.text((pad, 8), "390px  %s  —  Chromium · Firefox · WebKit" % tab.title(),
                  fill=(226, 232, 240), font=font)
        x = pad
        for engine, im in scaled:
            draw.text((x, pad + 4), engine, fill=(159, 170, 185), font=small)
            sheet.paste(im, (x, pad + label_h))
            x += im.width + gap
        dest = os.path.join(out, "sheet-%s.png" % tab)
        sheet.save(dest, optimize=True)
        paths.append(dest)
        print("wrote", dest)
    return paths


def goto_app(page, app_port):
    page.goto("http://127.0.0.1:%d/" % app_port, wait_until="load")
    page.get_by_text("Live", exact=True).first.wait_for(timeout=20000)
    page.evaluate("() => document.fonts.ready")
    page.wait_for_timeout(200)


def run_engine(pw, engine, headed, app_port, init_script, out, console_sink, findings):
    launcher = getattr(pw, engine)
    browser = launcher.launch(headless=not headed)
    version = browser.version
    print("%s %s" % (engine, version))
    try:
        for width, height, wname in VIEWPORTS:
          try:
            ctx = browser.new_context(**context_kwargs(pw, engine, width, height))
            ctx.add_init_script(init_script)
            page = ctx.new_page()
            attach_logging(page, console_sink, engine, wname)
            try:
                goto_app(page, app_port)
            except Exception as e:
                findings.append({
                    "severity": "critical",
                    "engine": engine,
                    "width": wname,
                    "tab": "load",
                    "issue": "App failed to load: %s" % e,
                })
                screenshot(page, os.path.join(out, "%s-%s-load-failed.png" % (engine, wname)))
                ctx.close()
                continue

            # Visit every tab and probe it.
            for tab_id, label in TABS:
                click_tab(page, label)
                if tab_id == "help":
                    # Help is a sheet. Open one topic so the wording is in the shot.
                    try:
                        help_dlg = page.get_by_role("dialog", name="Help")
                        help_dlg.wait_for(state="visible", timeout=8000)
                        help_dlg.get_by_role(
                            "button"
                        ).filter(has_text="The button on the box").first.click()
                        page.wait_for_timeout(250)
                    except Exception as e:
                        findings.append({
                            "severity": "high",
                            "engine": engine,
                            "width": wname,
                            "tab": "help",
                            "issue": "Could not open a Help topic: %s" % e,
                        })
                if tab_id == "charts":
                    page.wait_for_timeout(400)
                path = os.path.join(out, "%s-%s-%s.png" % (engine, wname, tab_id))
                screenshot(page, path)
                print("wrote", path)
                try:
                    probe = page.evaluate(PROBE_JS)
                except Exception as e:
                    probe = {"error": str(e)}
                # Attach probe findings.
                if isinstance(probe, dict):
                    for item in probe.get("issues") or []:
                        findings.append({
                            "severity": "medium",
                            "engine": engine,
                            "width": wname,
                            "tab": tab_id,
                            "issue": "Horizontal overflow: %s" % item,
                            "probe": item,
                        })
                    for item in probe.get("clipped") or []:
                        findings.append({
                            "severity": "medium",
                            "engine": engine,
                            "width": wname,
                            "tab": tab_id,
                            "issue": "Clipped text %r" % item.get("text"),
                            "probe": item,
                        })
                    fonts = probe.get("fonts") or {}
                    if fonts.get("inter") is False:
                        findings.append({
                            "severity": "medium",
                            "engine": engine,
                            "width": wname,
                            "tab": tab_id,
                            "issue": "Inter font did not report as loaded (document.fonts.check).",
                        })
                    if fonts.get("mono") is False and tab_id in ("readings", "charts", "health"):
                        findings.append({
                            "severity": "low",
                            "engine": engine,
                            "width": wname,
                            "tab": tab_id,
                            "issue": "JetBrains Mono did not report as loaded (document.fonts.check).",
                        })
                    if probe.get("hiddenIcons"):
                        findings.append({
                            "severity": "high",
                            "engine": engine,
                            "width": wname,
                            "tab": tab_id,
                            "issue": "One or more nav/header icons have a zero-size box.",
                            "probe": probe["hiddenIcons"],
                        })
                    # Stash supports once per engine (from the first tab).
                    probe["_engine"] = engine
                    probe["_width"] = wname
                    probe["_tab"] = tab_id
                    findings.append({
                        "severity": "probe",
                        "engine": engine,
                        "width": wname,
                        "tab": tab_id,
                        "issue": "probe",
                        "probe": probe,
                    })

            # Chart range switching and (on phone) the detail bottom sheet.
            click_tab(page, "Charts")
            page.wait_for_timeout(400)
            try:
                open_chart_detail(page)
                switch_ranges(page, findings, engine, wname)
                if wname == "390":
                    screenshot(
                        page,
                        os.path.join(out, "%s-390-charts-sheet.png" % engine),
                    )
                    drag_sheet_down(page, findings, engine, wname, "charts")
                dismiss_dialogs(page)
            except Exception as e:
                findings.append({
                    "severity": "high",
                    "engine": engine,
                    "width": wname,
                    "tab": "charts",
                    "issue": "Chart detail / range switching failed: %s" % e,
                })

            # Settings bottom sheet drag, phone widths only.
            if wname in ("390", "820"):
                try:
                    page.get_by_role("button", name="Settings").first.click()
                    page.get_by_role("dialog", name="Settings").wait_for(
                        state="visible", timeout=8000
                    )
                    page.wait_for_timeout(200)
                    drag_sheet_down(page, findings, engine, wname, "settings")
                    # If it is still open, close it so the next step is clean.
                    dlg = page.get_by_role("dialog", name="Settings")
                    if dlg.count() and dlg.first.is_visible():
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(200)
                except Exception as e:
                    findings.append({
                        "severity": "high",
                        "engine": engine,
                        "width": wname,
                        "tab": "settings",
                        "issue": "Settings sheet failed: %s" % e,
                    })

            # Readings odor Help link opens the Odor thresholds topic.
            click_tab(page, "Readings")
            page.wait_for_timeout(300)
            try:
                page.get_by_role("button", name="Help: Odor", exact=True).click()
                help_dlg = page.get_by_role("dialog", name="Help")
                help_dlg.wait_for(state="visible", timeout=8000)
                page.wait_for_timeout(400)
                title = help_dlg.locator("#help-title")
                got = title.inner_text() if title.count() else ""
                if got.strip() != "Odor thresholds":
                    findings.append({
                        "severity": "high",
                        "engine": engine,
                        "width": wname,
                        "tab": "help",
                        "issue": "Readings odor Help link did not open Odor thresholds (title=%r)." % got,
                    })
                if wname == "390":
                    screenshot(
                        page,
                        os.path.join(out, "%s-390-help-jump.png" % engine),
                    )
            except Exception as e:
                findings.append({
                    "severity": "high",
                    "engine": engine,
                    "width": wname,
                    "tab": "help",
                    "issue": "Readings odor Help jump failed: %s" % e,
                })

            ctx.close()
          except Exception as e:
            findings.append({
                "severity": "critical",
                "engine": engine,
                "width": wname,
                "tab": "*",
                "issue": "Viewport run failed: %s" % e,
            })
    finally:
        browser.close()
    return version


def write_console(path, sink):
    # Errors and warnings only in the summary file; report.json keeps everything.
    lines = []
    for ev in sink:
        if ev["type"] in ("error", "warning", "pageerror", "requestfailed") or str(ev["type"]).startswith("http-"):
            lines.append(
                "[%s %spx %s] %s: %s"
                % (ev["engine"], ev["width"], ev["when"], ev["type"], ev["text"])
            )
    with open(path, "w") as f:
        if not lines:
            f.write("No console errors, warnings, page errors, or failed requests.\n")
        else:
            f.write("\n".join(lines) + "\n")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=DEFAULT_OUT, help="screenshot directory")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--api-port", type=int, default=API_PORT_DEFAULT)
    parser.add_argument("--app-port", type=int, default=APP_PORT_DEFAULT)
    parser.add_argument(
        "--engines",
        default=",".join(ENGINES),
        help="comma-separated: chromium,firefox,webkit",
    )
    args = parser.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    from playwright.sync_api import sync_playwright

    work = "/tmp/smartfan-xbrowser"
    preview = start_preview(args.api_port, args.app_port, work)
    cfg = {
        "mode": "device",
        "host": "127.0.0.1",
        "httpPort": args.api_port,
        "room": "bathroom",
    }
    init = (
        "localStorage.setItem('smartfan.settings', %s);"
        "localStorage.setItem('smartfan.theme', 'dark');"
        % json.dumps(json.dumps(cfg))
    )

    console_sink = []
    findings = []
    versions = {}
    try:
        with sync_playwright() as pw:
            print("serving", preview["root"], "on", args.app_port, "api", args.api_port)
            for engine in [e.strip() for e in args.engines.split(",") if e.strip()]:
                try:
                    versions[engine] = run_engine(
                        pw, engine, args.headed, args.app_port,
                        init, out, console_sink, findings,
                    )
                except Exception:
                    traceback.print_exc()
                    findings.append({
                        "severity": "critical",
                        "engine": engine,
                        "width": "*",
                        "tab": "*",
                        "issue": "Engine run failed:\n" + traceback.format_exc(),
                    })
    finally:
        stop_preview(preview)

    sheets = make_contact_sheets(out)
    console_lines = write_console(os.path.join(out, "console.txt"), console_sink)
    report = {
        "versions": versions,
        "playwright": None,
        "out": out,
        "sheets": sheets,
        "console": console_sink,
        "findings": findings,
    }
    try:
        from playwright._repo_version import version as pw_ver
        report["playwright"] = pw_ver
    except Exception:
        pass
    with open(os.path.join(out, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    print("\nengines:", versions, "playwright", report["playwright"])
    visible = [f for f in findings if f["severity"] not in ("probe",)]
    print("issues:", len(visible), "console notable:", len(console_lines))
    for fnd in visible:
        print(
            "  [%s] %s %spx %s: %s"
            % (fnd["severity"], fnd["engine"], fnd["width"], fnd["tab"], fnd["issue"])
        )
    print("report:", os.path.join(out, "report.json"))


if __name__ == "__main__":
    main()
