// WebHome 扩展 for 毒舌电影 (dushe05.com)
// 原理: 不解析、不逆向。WebView 里站点自己的 JS 会把(加密的)播放地址解出来并让播放器去拉 m3u8/mp4。
//      本脚本只 hook fetch / XHR / <video>，截获播放器最终请求的真实媒体地址，推给 App 原生播放器。
//      —— cdndefend 验证由 WebView 自动通过；KKYS.safePlay 的 AES 解密也由站点自己完成，我们只取结果。
(function () {
  "use strict";
  var TAG = "[fm-dushe]";
  // 毒舌镜像站(任一失效, 把 WebHome 站点的 homePage 换成其它一个即可; 脚本自动适配, 无需改动)
  var DUSHE_HOSTS = ["dushe02.com", "dushe05.com", "dushe06.com"];
  // ★ fm.vod 桥接: 在毒舌网页里逛, 点"原生多集"→ 交给 dushe.py CSP 打开原生详情
  //   (整洁选集 01 02 03 + 切集 + 自动下一集 + 全11线路 + 加密线路也由 CSP 后端搞定)。
  //   DUSHE_CSP_KEY 必须等于"站点注入→通用CSP"导入 dushe.py 时那个【Key】字段的值。
  var DUSHE_CSP_KEY = "dushe";
  // 自动推原生播放器的策略:
  //   "smart"  = 智能探测: 截到流先看切片 CDN 是否支持 Accept-Ranges, 原生友好才自动推, 否则提示走网页(推荐)
  //   "always" = 截到就推(不探测; 不友好的会转圈)
  //   "off"    = 不自动推, 全留网页播, 想用原生点右下"▶ App播放"按钮(按钮始终强推, 不受探测限制)
  var PUSH_MODE = "off";
  // 选集(fm.vodInline): 毒舌加密线路其它集解不出 + 播放器渲染成大卡片 → 默认关, 走干净单集。
  // 想试多集(仅明文线路可能可切, 布局仍是卡片)可改 true。
  var EPISODE_LIST = false;
  var lastPushed = "";
  var lastPageKey = "";
  var captured = "";
  var smartProbed = false;   // 每个播放页只探测一次
  var friendlyState = null;  // 探测结果: true=原生友好 / false=不友好 / null=未知

  // ---------- 反 disable-devtool 护盾: 压制"devtool opened type=X"弹窗 + 阻止踹去百度 ----------
  // 尽量早执行(赶在站点脚本前)。非 100%(库是持续轮询、注入时机不定), 但能明显减少误弹/跳转。
  (function installAntiDebug() {
    function badUrl(u) { return typeof u === "string" && /baidu\.com|\/baidu/i.test(u); }
    // 1) 把 DisableDevtool 桩成空函数(若赶在网站调用它之前, 检测根本不启动)
    try {
      var stub = function () { return stub; };
      stub.isRunning = false; stub.isSuspend = true; stub.md5 = function () {}; stub.version = "0";
      stub.DetectorType = {}; stub.clearInterval = function () {}; stub.resume = function () {}; stub.suspend = function () {};
      try { Object.defineProperty(window, "DisableDevtool", { value: stub, writable: false, configurable: false }); }
      catch (e) { window.DisableDevtool = stub; }
    } catch (e) {}
    // 2) 吞掉 "devtool opened / debug" 的 alert/confirm
    try {
      var _alert = window.alert;
      window.alert = function (m) { if (typeof m === "string" && /devtool|opened|debug/i.test(m)) { log("blocked alert", m); return; } return _alert.apply(window, arguments); };
    } catch (e) {}
    try {
      var _confirm = window.confirm;
      window.confirm = function (m) { if (typeof m === "string" && /devtool|opened|debug/i.test(m)) { log("blocked confirm", m); return false; } return _confirm.apply(window, arguments); };
    } catch (e) {}
    // 3) 拦 location.assign / replace 跳百度
    var slice = Array.prototype.slice;
    ["assign", "replace"].forEach(function (mname) {
      try {
        var orig = window.location[mname] && window.location[mname].bind(window.location);
        if (!orig) return;
        window.location[mname] = function (u) { if (badUrl(u)) { log("blocked " + mname, u); return; } return orig.apply(null, slice.call(arguments)); };
      } catch (e) {}
    });
    // 4) 拦 window.open 跳百度
    try {
      var _open = window.open;
      window.open = function (u) { if (badUrl(u)) { log("blocked open", u); return null; } return _open.apply(window, arguments); };
    } catch (e) {}
  })();

  function isDusheHost() {
    var h = location.hostname || "";
    for (var i = 0; i < DUSHE_HOSTS.length; i++) { if (h.indexOf(DUSHE_HOSTS[i]) >= 0) return true; }
    return false;
  }

  function log() {
    var a = Array.prototype.slice.call(arguments);
    try { (typeof GM_log === "function" ? GM_log : console.log).apply(null, [TAG].concat(a)); } catch (e) {}
  }
  function whenFm() {
    if (window.fm) return Promise.resolve(window.fm);
    return new Promise(function (resolve) {
      window.addEventListener("fmsdk", function () { resolve(window.fm); }, { once: true });
    });
  }
  function toast(msg) {
    try { if (window.fm && fm.ext && fm.ext.toast) return fm.ext.toast(msg); } catch (e) {}
  }
  function absUrl(u) {
    try { return new URL(u, location.href).href; } catch (e) { return u; }
  }
  function isMedia(u) {
    if (!u || typeof u !== "string") return false;
    if (u.indexOf("blob:") === 0) return false;          // hls.js 的 MediaSource，不是真地址
    return /\.m3u8(\?|$)/i.test(u) || /\.mp4(\?|$)/i.test(u);
  }
  function isPlayPage() { return /\/play\//.test(location.pathname); }
  function isDetailPage() { return /\/detail\//.test(location.pathname); }
  function pageKey() { return location.pathname; }

  // 从 /detail/{id}.html 或 /play/{id}-{sid}-{nid}.html 取 vodId(CSP 详情用同一个 id)
  function dusheVodId() {
    var m = location.pathname.match(/\/(?:detail|play)\/(\d+)/);
    return m ? m[1] : "";
  }
  // ★ 调 dushe.py CSP 打开原生多集详情页
  function openNativeCsp() {
    var id = dusheVodId();
    if (!id) { toast("没识别到剧集ID(请在详情/播放页点)"); return; }
    whenFm().then(function (fm) {
      if (typeof fm.vod !== "function") { toast("当前App版本不支持 fm.vod, 请更新"); return; }
      try {
        fm.vod(DUSHE_CSP_KEY, id, pageTitle(), pagePoster() || "");
        toast("打开CSP源[" + DUSHE_CSP_KEY + "] id=" + id);  // 若随后跳到夸克/虎虎等别的源, 说明此key没匹配上,去对齐CSP的Key字段
        log("fm.vod ->", DUSHE_CSP_KEY, id);
      } catch (e) { toast("打开失败: " + (e && e.message)); log("fm.vod failed", e && e.message); }
    });
  }

  function pageTitle() {
    var h = document.querySelector("h1");
    var t = (h && h.textContent || "").trim();
    if (!t) t = (document.title || "").trim();
    t = t.replace(/[-_].*?(毒舌|在线观看|高清|免费|播放).*$/, "").trim();
    return t || (document.title || "毒舌电影").trim();
  }
  // 当前正在播的集名(选集里高亮的那个 / 兜底用 URL 的 nid), 用于单集标题显示"第几集"
  function currentEpisodeName() {
    try {
      var a = document.querySelector("a.episode-item-active, a.episode-item.active, .episode-item.active, .episode-item-active");
      if (a) { var t = (a.textContent || "").replace(/\s+/g, " ").trim(); if (t && t.length < 16) return t; }
      var m = location.pathname.match(/\/play\/\d+-\d+-(\d+)\.html/);
      return m ? m[1] : "";
    } catch (e) { return ""; }
  }
  function pagePoster() {
    try {
      var og = document.querySelector('meta[property="og:image"], meta[name="og:image"]');
      if (og && og.content) return og.content;
      var im = document.querySelector('.detail img, .stui-content__thumb, img.lazyload, img[data-original]');
      if (im) return im.getAttribute("data-original") || im.getAttribute("data-src") || im.src || "";
    } catch (e) {}
    return "";
  }

  function stdHeaders() {
    return {
      "User-Agent": navigator.userAgent,
      "Referer": location.origin + "/",
      "Origin": location.origin,
      "Cookie": document.cookie || ""
    };
  }

  // ---------- 智能探测: 切片 CDN 是否支持 Accept-Ranges(决定原生播放器能否顺播) ----------
  function absTo(u, base) { try { return new URL(u, base).href; } catch (e) { return u; } }
  function firstUri(text) {
    var ls = String(text || "").split(/\r?\n/);
    for (var i = 0; i < ls.length; i++) { var l = ls[i].trim(); if (l && l.charAt(0) !== "#") return l; }
    return "";
  }
  function fmReqRaw(fm, url, opts) {
    try { return Promise.resolve(fm.req(url, opts)).catch(function () { return null; }); }
    catch (e) { return Promise.resolve(null); }
  }
  function bodyText(r) { if (!r) return ""; var b = r.body; return b == null ? "" : (typeof b === "string" ? b : ""); }
  function headerOf(r, name) {
    if (!r || !r.headers) return "";
    name = name.toLowerCase(); var h = r.headers, v = "";
    for (var k in h) { if (k.toLowerCase() === name) { v = h[k]; break; } }
    if (Object.prototype.toString.call(v) === "[object Array]") v = v[0];
    return v || "";
  }
  // 返回 Promise<true=友好 / false=不友好 / null=测不出>
  function probeFriendly(fm, m3u8Url, headers) {
    var opt = { headers: headers, credentials: "include", responseType: "text" };
    function checkSeg(text, vurl) {
      var seg = firstUri(text); if (!seg) return null;
      return fmReqRaw(fm, absTo(seg, vurl), { method: "HEAD", headers: headers, credentials: "include" })
        .then(function (rs) {
          if (!rs) return null;
          var ar = headerOf(rs, "accept-ranges");
          return /bytes/i.test(ar);
        });
    }
    return fmReqRaw(fm, m3u8Url, opt).then(function (r) {
      var text = bodyText(r); if (!text || text.indexOf("#EXTM3U") < 0) return null;
      if (/#EXT-X-STREAM-INF/i.test(text)) {
        var v = firstUri(text); if (!v) return null;
        var vurl = absTo(v, m3u8Url);
        return fmReqRaw(fm, vurl, opt).then(function (r2) { return checkSeg(bodyText(r2), vurl); });
      }
      return checkSeg(text, m3u8Url);
    });
  }

  // ---------- 选集(fm.vodInline): 读当前线路整部剧的集列表 ----------
  function buildEpisodes() {
    try {
      var m = location.pathname.match(/\/play\/(\d+)-(\d+)-(\d+)\.html/);
      if (!m) return null;
      var id = m[1], sid = m[2], curNid = m[3];
      var pre = "/play/" + id + "-" + sid + "-"; // 只取当前线路的集
      // 渲染后的选集项: <a href="/play/{id}-{sid}-{nid}.html" class="episode-item"><span>名称</span></a>
      var anchors = document.querySelectorAll('a[href*="' + pre + '"]');
      var eps = [], seen = {}, curName = "", i, nm;
      for (i = 0; i < anchors.length; i++) {
        var href = anchors[i].getAttribute("href") || "";
        var idx = href.indexOf(pre); if (idx < 0) continue;
        nm = href.slice(idx + pre.length).match(/^(\d+)\.html/); if (!nm) continue;
        var nid = nm[1];
        if (seen[nid]) continue; seen[nid] = 1;
        var name = (anchors[i].textContent || "").replace(/\s+/g, " ").trim();
        if (!name || /立即播放|点击选择|选集播放|切换线路/.test(name)) name = "第" + (eps.length + 1) + "集";
        var ep = { name: name };
        if (nid === curNid && captured) { ep.url = captured; curName = name; }
        else { ep.pageUrl = absUrl(href); ep.resolve = true; }
        eps.push(ep);
      }
      return eps.length > 1 ? { episodes: eps, mark: curName || eps[0].name } : null;
    } catch (e) { return null; }
  }
  function extractM3u8(html) {
    if (!html) return "";
    var m = html.match(/playSource\s*=\s*\{[\s\S]{0,200}?src['"]?\s*:\s*['"](https?:\/\/[^'"\\]+\.(?:m3u8|mp4)[^'"\\]*)/i)
         || html.match(/player_aaaa[\s\S]{0,300}?["']url["']\s*:\s*["']([^"']+\.(?:m3u8|mp4)[^"']*)["']/i)
         || html.match(/(https?:\/\/[^"'\\ ]+\.m3u8[^"'\\ ]*)/i);
    return m ? m[1].replace(/\\\//g, "/") : "";
  }
  function fmReqText(fm, url) {
    try {
      // 带上 cdndefend cookie + Referer/UA, 否则原生请求拿到的是验证页, 取不到 m3u8
      return Promise.resolve(fm.req(url, { responseType: "text", credentials: "include",
        headers: { "Cookie": document.cookie || "", "User-Agent": navigator.userAgent, "Referer": location.origin + "/" } }))
        .then(function (r) { return (r && (r.body != null ? r.body : r.text)) || ""; })
        .catch(function () { return ""; });
    } catch (e) { return Promise.resolve(""); }
  }
  // Native 在用户点"待解析"集时回调它拿真实地址(明文线路可解; KKYS 加密集可能取不到 → 返回空)
  window.__fmWebHomeInlineResolver = function (ep) {
    return whenFm().then(function (fm) {
      if (!ep || !ep.pageUrl || !fm.req) return {};
      return fmReqText(fm, ep.pageUrl).then(function (h) { var u = extractM3u8(h); return u ? { url: u } : {}; });
    });
  };

  // 把截获到的地址推给 App 原生播放器(优先 vodInline 带选集, 失败回退单集裸播)
  function pushPlay(url, auto) {
    url = absUrl(url);
    if (!url || url === lastPushed) return;
    if (url.length > 8192) { log("url too long, skip", url.length); toast("该源地址异常,改用网页播放"); return; }
    lastPushed = url;
    captured = url;
    pausePageVideos();
    whenFm().then(function (fm) {
      var pic = pagePoster();
      // 优先选集(仅当 App 支持 fm.vodInline; 旧版无此方法 → 自动走下面单集)
      var info = (EPISODE_LIST && typeof fm.vodInline === "function") ? buildEpisodes() : null;
      if (info) {
        try {
          fm.vodInline({ vod_name: pageTitle(), vod_pic: pic, wallPic: pic, vod_play_from: "毒舌", mark: info.mark, episodes: info.episodes });
          toast(auto ? "已用App播放(选集)" : "正在播放(选集)");
          log("vodInline", info.episodes.length, "集 | mark", info.mark);
          return;
        } catch (e) { log("vodInline failed", e && e.message); }
      }
      // 回退: 单集裸播(不传 headers, 绕网关; CDN 开放无需请求头)
      try {
        var opts = {};
        if (pic) { opts.pic = pic; opts.wallPic = pic; }
        var title = pageTitle();
        var ep = currentEpisodeName();
        if (ep) title += " " + ep; // 标题带上当前集, 方便看在播第几集
        fm.play(url, title, opts);
        toast(auto ? "已用App播放" : "正在播放");
        log("play(raw single) ->", title, "|", url);
      } catch (e) { log("play failed", e && e.message); }
    });
  }

  function pausePageVideos() {
    try {
      var vs = document.querySelectorAll("video");
      for (var i = 0; i < vs.length; i++) { try { vs[i].pause(); vs[i].muted = true; } catch (e) {} }
    } catch (e) {}
  }

  // 翻页/换集时复位，允许重新截获
  function resetIfPageChanged() {
    var k = pageKey();
    if (k !== lastPageKey) { lastPageKey = k; lastPushed = ""; captured = ""; smartProbed = false; friendlyState = null; }
  }

  // ---------- 网络 hook：截获媒体地址 ----------
  function onMaybeMedia(u) {
    if (!isDusheHost() || !isPlayPage()) return;
    resetIfPageChanged();
    if (!isMedia(u)) return;
    var url = absUrl(u);
    captured = url;
    if (PUSH_MODE === "always") { pushPlay(url, true); return; }
    if (PUSH_MODE !== "smart") return; // off: 只记住地址, 用右下按钮强推
    if (smartProbed) return;
    smartProbed = true;
    whenFm().then(function (fm) {
      if (!fm.req) { pushPlay(url, true); return; }
      probeFriendly(fm, url, stdHeaders()).then(function (ok) {
        friendlyState = ok;
        if (ok === false) toast("此源原生可能不兼容,可改网页播放");
        else pushPlay(url, true);
      }).catch(function () { pushPlay(url, true); });
    });
  }

  // 让改写过的函数 toString 仍返回原生样子, 降低站点反调试(disable-devtool)误报
  function cloak(fn, orig) {
    try { fn.toString = function () { return Function.prototype.toString.call(orig); }; } catch (e) {}
    return fn;
  }
  // fetch
  var origFetch = window.fetch;
  if (origFetch) {
    window.fetch = cloak(function (input, init) {
      try {
        var u = (typeof input === "string") ? input : (input && input.url);
        onMaybeMedia(u);
      } catch (e) {}
      return origFetch.apply(this, arguments);
    }, origFetch);
  }
  // XMLHttpRequest
  var origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = cloak(function (method, url) {
    try { onMaybeMedia(url); } catch (e) {}
    return origOpen.apply(this, arguments);
  }, origOpen);
  // <video> src 兜底（mp4 直链型）
  function watchVideoSrc() {
    try {
      var vs = document.querySelectorAll("video");
      for (var i = 0; i < vs.length; i++) {
        var src = vs[i].currentSrc || vs[i].src;
        if (src && isMedia(src)) { onMaybeMedia(src); }
      }
    } catch (e) {}
  }

  // ---------- 全屏(原生失败则 CSS 铺满兜底; 兼容 iframe 播放器) ----------
  var FILL_Z = 2147483000; // 铺满层级: 低于按钮(下面按钮 z-index 更高), 否则会盖住按钮没法退出
  function pickLargest(sel) {
    var els = document.querySelectorAll(sel), best = null, area = -1;
    for (var i = 0; i < els.length; i++) {
      var e = els[i], a = (e.clientWidth || 0) * (e.clientHeight || 0);
      if (a > area) { area = a; best = e; }
    }
    return best;
  }
  function cssExit() {
    var el = window.__fmFsEl; if (!el) return;
    el.setAttribute("style", window.__fmFsOld || "");
    window.__fmFsEl = null;
    try { document.documentElement.style.overflow = ""; } catch (e) {}
    toast("退出全屏");
  }
  function cssFill(el) {
    window.__fmFsEl = el;
    window.__fmFsOld = el.getAttribute("style") || "";
    el.style.cssText = "position:fixed!important;top:0!important;left:0!important;right:0!important;bottom:0!important;" +
      "width:100vw!important;height:100vh!important;max-width:none!important;max-height:none!important;margin:0!important;" +
      "z-index:" + FILL_Z + "!important;background:#000!important;object-fit:contain!important;";
    try { document.documentElement.style.overflow = "hidden"; } catch (e) {}
    try { if (el.play) el.play(); } catch (e) {}
    toast("已全屏(再点⛶退出)");
  }
  function goFullscreen() {
    if (window.__fmFsEl) { cssExit(); return; } // 已 CSS 全屏 -> 退出
    var v = pickLargest("video");
    var target = v || pickLargest("iframe"); // 没有 video 就铺 iframe 播放器
    if (!target) { toast("没找到播放器,先开始播放"); return; }
    if (v) { // 先试原生全屏(仅 video)
      var req = v.requestFullscreen || v.webkitRequestFullscreen || v.webkitEnterFullscreen || v.mozRequestFullScreen;
      if (req) { try { req.call(v); } catch (e) {} }
    }
    try { if (screen.orientation && screen.orientation.lock) screen.orientation.lock("landscape").catch(function () {}); } catch (e) {}
    // 350ms 后没真正进入原生全屏 -> CSS 铺满兜底
    setTimeout(function () {
      var native = document.fullscreenElement || document.webkitFullscreenElement || (v && v.webkitDisplayingFullscreen);
      if (!native) cssFill(target);
    }, 350);
  }

  // 没截到网络请求时, 直接从页面读取播放地址(直链型线路 playSource.src 就在页面里)
  function readPageSource() {
    try {
      if (window.playSource && window.playSource.src && /^https?:/i.test(window.playSource.src)) return window.playSource.src;
    } catch (e) {}
    try {
      var html = document.documentElement.innerHTML;
      var m = html.match(/playSource\s*=\s*\{[\s\S]{0,200}?src['"]?\s*:\s*['"](https?:\/\/[^'"\\]+\.(?:m3u8|mp4)[^'"\\]*)/i);
      if (m) return m[1];
      var m2 = html.match(/(https?:\/\/[^"'\\ ]+\.m3u8[^"'\\ ]*)/i); // 兜底: 页面第一个直链 m3u8
      if (m2) return m2[1];
    } catch (e) {}
    return "";
  }

  // ---------- 浮动按钮: App播放 + 全屏 ----------
  function ensureButton() {
    var onVod = isPlayPage() || isDetailPage();
    if (!onVod) {
      ["fm-dushe-csp", "fm-dushe-btn", "fm-dushe-fs"].forEach(function (id) {
        var e = document.getElementById(id); if (e) e.remove();
      });
      return;
    }
    // ★ 原生多集按钮(详情页 + 播放页都显示) → 走 dushe.py CSP
    if (!document.getElementById("fm-dushe-csp")) {
      var c = document.createElement("div");
      c.id = "fm-dushe-csp";
      c.textContent = "📺 原生多集";
      c.style.cssText = "position:fixed;right:12px;bottom:196px;z-index:2147483600;background:#1db954;color:#fff;" +
        "padding:10px 16px;border-radius:22px;font-size:14px;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,.4);cursor:pointer;";
      c.addEventListener("click", openNativeCsp);
      document.body.appendChild(c);
    }
    // 单集裸播 + 全屏: 只在播放页显示(它们针对网页内播放器)
    if (!isPlayPage()) {
      var eb = document.getElementById("fm-dushe-btn"); if (eb) eb.remove();
      var efs = document.getElementById("fm-dushe-fs"); if (efs) efs.remove();
      return;
    }
    if (!document.getElementById("fm-dushe-btn")) {
      var b = document.createElement("div");
      b.id = "fm-dushe-btn";
      b.style.cssText = "position:fixed;right:12px;bottom:84px;z-index:2147483600;color:#fff;" +
        "padding:10px 16px;border-radius:22px;font-size:14px;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,.4);cursor:pointer;";
      b.addEventListener("click", function () {
        var u = captured || readPageSource(); // 没截到就直接从页面读 playSource.src
        if (u) { lastPushed = ""; pushPlay(u, false); }
        else { watchVideoSrc(); toast("没找到播放地址(此线路可能仅供网页/官方App)"); }
      });
      document.body.appendChild(b);
    }
    if (!document.getElementById("fm-dushe-fs")) {
      var f = document.createElement("div");
      f.id = "fm-dushe-fs";
      f.textContent = "⛶ 全屏";
      f.style.cssText = "position:fixed;right:12px;bottom:140px;z-index:2147483600;background:#222;color:#fff;" +
        "padding:10px 16px;border-radius:22px;font-size:14px;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,.4);cursor:pointer;";
      f.addEventListener("click", goFullscreen);
      document.body.appendChild(f);
    }
    updateButton();
  }
  // App播放按钮按探测结果变色/变字, 帮你判断要不要点
  function updateButton() {
    var b = document.getElementById("fm-dushe-btn"); if (!b) return;
    if (friendlyState === true) { b.style.background = "#1db954"; b.textContent = "▶ App播放 ✓友好"; }
    else if (friendlyState === false) { b.style.background = "#888"; b.textContent = "▶ App播放(建议网页)"; }
    else { b.style.background = "#e50914"; b.textContent = "▶ App播放"; }
  }

  // 自动关掉"仅供APP端观看/下载官方App"拦截弹窗(DOM 前缀 op__), 避免它盖住界面、卡住换线路
  function killBlockPopup() {
    try {
      var els = document.querySelectorAll('[id^="op__"],[class^="op__"],[class*=" op__"]');
      var hit = false;
      for (var i = 0; i < els.length; i++) {
        var e = els[i];
        if (e && e.style && e.style.display !== "none") { e.style.display = "none"; hit = true; }
      }
      if (hit) { try { document.documentElement.style.overflow = ""; document.body.style.overflow = ""; } catch (e) {} }
    } catch (e) {}
  }

  // ---------- 轮询：复位 + 兜底扫描 + 按钮 + 关拦截弹窗 ----------
  setInterval(function () {
    if (!isDusheHost()) return;
    killBlockPopup();
    resetIfPageChanged();
    if (isPlayPage()) { watchVideoSrc(); ensureButton(); }
    else { ensureButton(); }
  }, 1200);

  whenFm().then(function () { log("SDK ready on", location.href); });
  log("loaded", location.href);
})();
