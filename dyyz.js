// WebHome 扩展 for 电影驿站 (dyyz.top / dyyz.cc / dyyz.one / dyyz2.app)
// 在 WebView 里浏览 dyyz 网站(含网站自带搜索框/分类), 播放页读 player_aaaa.url(明文 m3u8)推 App 原生播放器。
// dyyz 无反爬, 播放地址明文, 比毒舌简单。配套 CSP 源见 电影驿站.py(原生界面)。
(function () {
  "use strict";
  var TAG = "[fm-dyyz]";
  // 镜像站(任一失效, 把 WebHome 的 homePage 换成其它一个即可; 脚本自动适配)
  var DYYZ_HOSTS = ["dyyz.top", "dyyz.cc", "dyyz.one", "dyyz2.app", "dyyz"];
  // "off" = 不自动推, 浏览网站, 想原生时点右下"▶ App播放";  "always" = 进播放页自动推原生
  var PUSH_MODE = "off";
  var lastPushed = "", lastPageKey = "", captured = "";

  function isDyyzHost() {
    var h = location.hostname || "";
    for (var i = 0; i < DYYZ_HOSTS.length; i++) { if (h.indexOf(DYYZ_HOSTS[i]) >= 0) return true; }
    return false;
  }
  function log() {
    var a = Array.prototype.slice.call(arguments);
    try { (typeof GM_log === "function" ? GM_log : console.log).apply(null, [TAG].concat(a)); } catch (e) {}
  }
  function whenFm() {
    if (window.fm) return Promise.resolve(window.fm);
    return new Promise(function (resolve) { window.addEventListener("fmsdk", function () { resolve(window.fm); }, { once: true }); });
  }
  function toast(msg) { try { if (window.fm && fm.ext && fm.ext.toast) return fm.ext.toast(msg); } catch (e) {} }
  function absUrl(u) { try { return new URL(u, location.href).href; } catch (e) { return u; } }
  function isMedia(u) {
    if (!u || typeof u !== "string") return false;
    if (u.indexOf("blob:") === 0) return false;
    return /\.m3u8(\?|$)/i.test(u) || /\.mp4(\?|$)/i.test(u);
  }
  function isPlayPage() {
    if (/\/vodplay\//.test(location.pathname)) return true;
    try { if (window.player_aaaa && window.player_aaaa.url) return true; } catch (e) {} // URL 不是 /vodplay/ 但有播放数据也算
    return false;
  }
  function pageKey() { return location.pathname; }

  function pageTitle() {
    try {
      if (window.player_aaaa && window.player_aaaa.vod_data && window.player_aaaa.vod_data.vod_name)
        return String(window.player_aaaa.vod_data.vod_name).trim();
    } catch (e) {}
    var h = document.querySelector("h1");
    var t = (h && h.textContent || document.title || "").trim();
    return t.replace(/[-_].*?(电影驿站|在线观看|高清|免费|播放|dyyz).*$/i, "").trim() || "电影驿站";
  }
  function pagePoster() {
    try {
      var og = document.querySelector('meta[property="og:image"], meta[name="og:image"]');
      if (og && og.content) return og.content;
      var im = document.querySelector('.stui-content__thumb, .detail img, img.lazyload, img[data-original]');
      if (im) return im.getAttribute("data-original") || im.getAttribute("data-src") || im.src || "";
    } catch (e) {}
    return "";
  }
  // dyyz 播放地址直接在页面 player_aaaa.url(明文), 优先读它
  function readPageSource() {
    try {
      if (window.player_aaaa && window.player_aaaa.url) {
        var u = String(window.player_aaaa.url).replace(/\\\//g, "/");
        if (/^https?:/i.test(u)) return u;
      }
    } catch (e) {}
    try {
      var html = document.documentElement.innerHTML;
      var m = html.match(/player_aaaa\s*=\s*\{[\s\S]*?["']url["']\s*:\s*["']([^"']+)["']/i);
      if (m) return m[1].replace(/\\\//g, "/");
      var m2 = html.match(/(https?:\\?\/\\?\/[^"'\\ ]+?\.(?:m3u8|mp4)[^"'\\ ]*)/i);
      if (m2) return m2[1].replace(/\\\//g, "/");
    } catch (e) {}
    return "";
  }

  function pausePageVideos() {
    try { var vs = document.querySelectorAll("video"); for (var i = 0; i < vs.length; i++) { try { vs[i].pause(); vs[i].muted = true; } catch (e) {} } } catch (e) {}
  }
  // 裸播: 不传 headers/credentials, 不走本地网关(避免网关 HEAD 在部分 CDN 上 502 转圈)
  function pushPlay(url, auto) {
    url = absUrl(url);
    if (!url || url === lastPushed) return;
    if (url.length > 8192) { toast("该源地址异常,改用网页播放"); return; }
    lastPushed = url; captured = url;
    pausePageVideos();
    whenFm().then(function (fm) {
      try {
        var opts = {}; var pic = pagePoster();
        if (pic) { opts.pic = pic; opts.wallPic = pic; }
        fm.play(url, pageTitle(), opts);
        toast(auto ? "已用App播放" : "正在播放");
        log("play ->", pageTitle(), "|", url);
      } catch (e) { log("play failed", e && e.message); }
    });
  }

  function resetIfPageChanged() {
    var k = pageKey();
    if (k !== lastPageKey) { lastPageKey = k; lastPushed = ""; captured = ""; }
  }
  function onMaybeMedia(u) {
    if (!isDyyzHost() || !isPlayPage()) return;
    resetIfPageChanged();
    if (!isMedia(u)) return;
    captured = absUrl(u);
    if (PUSH_MODE === "always") pushPlay(captured, true);
  }
  function cloak(fn, orig) { try { fn.toString = function () { return Function.prototype.toString.call(orig); }; } catch (e) {} return fn; }
  var origFetch = window.fetch;
  if (origFetch) {
    window.fetch = cloak(function (input) {
      try { onMaybeMedia(typeof input === "string" ? input : (input && input.url)); } catch (e) {}
      return origFetch.apply(this, arguments);
    }, origFetch);
  }
  var origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = cloak(function (method, url) {
    try { onMaybeMedia(url); } catch (e) {}
    return origOpen.apply(this, arguments);
  }, origOpen);
  function watchVideoSrc() {
    try { var vs = document.querySelectorAll("video"); for (var i = 0; i < vs.length; i++) { var src = vs[i].currentSrc || vs[i].src; if (src && isMedia(src)) onMaybeMedia(src); } } catch (e) {}
  }

  // ---------- 全屏 ----------
  var FILL_Z = 2147483000;
  function pickLargest(sel) {
    var els = document.querySelectorAll(sel), best = null, area = -1;
    for (var i = 0; i < els.length; i++) { var e = els[i], a = (e.clientWidth || 0) * (e.clientHeight || 0); if (a > area) { area = a; best = e; } }
    return best;
  }
  function cssExit() {
    var el = window.__fmFsEl; if (!el) return;
    el.setAttribute("style", window.__fmFsOld || ""); window.__fmFsEl = null;
    try { document.documentElement.style.overflow = ""; } catch (e) {} toast("退出全屏");
  }
  function cssFill(el) {
    window.__fmFsEl = el; window.__fmFsOld = el.getAttribute("style") || "";
    el.style.cssText = "position:fixed!important;top:0!important;left:0!important;right:0!important;bottom:0!important;width:100vw!important;height:100vh!important;max-width:none!important;max-height:none!important;margin:0!important;z-index:" + FILL_Z + "!important;background:#000!important;object-fit:contain!important;";
    try { document.documentElement.style.overflow = "hidden"; } catch (e) {}
    try { if (el.play) el.play(); } catch (e) {} toast("已全屏(再点⛶退出)");
  }
  function goFullscreen() {
    if (window.__fmFsEl) { cssExit(); return; }
    var v = pickLargest("video"), target = v || pickLargest("iframe");
    if (!target) { toast("没找到播放器,先开始播放"); return; }
    if (v) { var req = v.requestFullscreen || v.webkitRequestFullscreen || v.webkitEnterFullscreen || v.mozRequestFullScreen; if (req) { try { req.call(v); } catch (e) {} } }
    try { if (screen.orientation && screen.orientation.lock) screen.orientation.lock("landscape").catch(function () {}); } catch (e) {}
    setTimeout(function () { var native = document.fullscreenElement || document.webkitFullscreenElement || (v && v.webkitDisplayingFullscreen); if (!native) cssFill(target); }, 350);
  }

  // ---------- 浮动按钮 ----------
  function ensureButton() {
    if (!isPlayPage()) {
      var ex = document.getElementById("fm-dyyz-btn"); if (ex) ex.remove();
      var ef = document.getElementById("fm-dyyz-fs"); if (ef) ef.remove();
      return;
    }
    if (!document.getElementById("fm-dyyz-btn")) {
      var b = document.createElement("div");
      b.id = "fm-dyyz-btn"; b.textContent = "▶ App播放";
      b.style.cssText = "position:fixed;right:12px;bottom:84px;z-index:2147483600;background:#e50914;color:#fff;padding:10px 16px;border-radius:22px;font-size:14px;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,.4);cursor:pointer;";
      b.addEventListener("click", function () {
        var u = captured || readPageSource();
        if (u) { lastPushed = ""; pushPlay(u, false); }
        else { watchVideoSrc(); toast("没找到播放地址,换条线路试试"); }
      });
      document.body.appendChild(b);
    }
    if (!document.getElementById("fm-dyyz-fs")) {
      var f = document.createElement("div");
      f.id = "fm-dyyz-fs"; f.textContent = "⛶ 全屏";
      f.style.cssText = "position:fixed;right:12px;bottom:140px;z-index:2147483600;background:#222;color:#fff;padding:10px 16px;border-radius:22px;font-size:14px;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,.4);cursor:pointer;";
      f.addEventListener("click", goFullscreen);
      document.body.appendChild(f);
    }
  }

  setInterval(function () {
    if (!isDyyzHost()) return;
    resetIfPageChanged();
    if (isPlayPage()) { if (!captured) { var u = readPageSource(); if (u) captured = u; } watchVideoSrc(); }
    ensureButton();
  }, 1200);

  log("loaded", location.href);
})();
