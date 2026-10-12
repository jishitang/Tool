# -*- coding: utf-8 -*-
# 哔哩(B站) py 源 —— FongMi/ok影视 CSP python spider
# 工作原理: 爬 B 站官方公开接口(搜索/详情/playurl), WBI 签名 + cookie
# cookie 传入: all.json 里本条目的 ext 字段 (内联 cookie 字符串, 或填一个返回 cookie 的 URL)
# 清晰度: mp4 单链(fnval=1), 封顶约 1080P, 稳。以后要 4K/HDR 再升 dash。
# VER = "v1"
import sys, time, json, re, hashlib
from urllib.parse import urlencode, quote

sys.path.append('..')
try:
    from base.spider import Spider
except Exception:
    class Spider:  # 本地离线测试用的桩
        def init(self, extend=''): pass

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# WBI 固定重排表 (B站 web 端常量)
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_SEARCH = "https://api.bilibili.com/x/web-interface/wbi/search/type"
API_VIEW = "https://api.bilibili.com/x/web-interface/wbi/view"
API_PAGELIST = "https://api.bilibili.com/x/player/pagelist"
API_PLAYURL = "https://api.bilibili.com/x/player/wbi/playurl"
REFERER = "https://www.bilibili.com"

# 清晰度 qn → 线路名(非会员最高 1080P; 高档会员专属, 这里也列着, 有就显示)
QUAL_MAP = [(120, '4K'), (116, '1080P60'), (112, '1080P高码'), (80, '1080P'),
            (74, '720P60'), (64, '720P'), (32, '480P'), (16, '360P')]
QUAL_LABEL = dict(QUAL_MAP)


class Spider(Spider):

    def init(self, extend=''):
        ck = (extend or '').strip()
        # 兼容"方式B": ext 填一个返回 cookie 的 URL, 就去拉; 否则当 cookie 本身
        if ck.startswith('http'):
            try:
                ck = requests.get(ck, timeout=8).text.strip()
            except Exception:
                ck = ''
        self.cookie = ck
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': UA, 'Referer': REFERER})
        self._primed = False
        self._wbi = None        # (img_key, sub_key)
        self._wbi_ts = 0
        self._mpd_cache = {}    # token -> MPD字符串(给 localProxy 吐 dash 清单)
        self.session.trust_env = False   # chaquo: 不吃系统代理环境变量
        return self

    # B站对 view/playurl 等端点有风控: 必须先有 buvid3 cookie 才放行。
    # 访问一次主站拿 buvid3/b_nut, 再把用户 cookie(SESSDATA 等) 注入同一 jar。
    def _ensure(self):
        if self._primed:
            return
        try:
            self.session.get('https://www.bilibili.com/', timeout=10)
        except Exception:
            pass
        if self.cookie:
            for part in self.cookie.split(';'):
                if '=' in part:
                    k, v = part.strip().split('=', 1)
                    try:
                        self.session.cookies.set(k.strip(), v.strip(), domain='.bilibili.com')
                    except Exception:
                        pass
        self._primed = True

    def getName(self):
        return "哔哩"

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    # ---------------- WBI 签名 ----------------
    def _get_wbi_keys(self):
        now = time.time()
        if self._wbi and all(self._wbi) and (now - self._wbi_ts) < 6 * 3600:  # 按天缓存, 这里 6h
            return self._wbi
        self._ensure()   # nav 也需要 buvid3 才稳, 先备好会话
        try:
            r = self.session.get(API_NAV, timeout=10).json()
            wi = r['data']['wbi_img']
            img_key = wi['img_url'].rsplit('/', 1)[-1].split('.')[0]
            sub_key = wi['sub_url'].rsplit('/', 1)[-1].split('.')[0]
            if img_key and sub_key:   # 只缓存有效 key, 失败下次重取
                self._wbi = (img_key, sub_key)
                self._wbi_ts = now
        except Exception:
            pass
        return self._wbi or ('', '')

    def _mixin_key(self, orig):
        return ''.join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]

    def _sign(self, params):
        img_key, sub_key = self._get_wbi_keys()
        mixin = self._mixin_key(img_key + sub_key)
        p = dict(params)
        p['wts'] = int(time.time())
        p = {k: p[k] for k in sorted(p)}
        clean = {k: ''.join(c for c in str(v) if c not in "!'()*") for k, v in p.items()}
        query = urlencode(clean)
        p['w_rid'] = hashlib.md5((query + mixin).encode('utf-8')).hexdigest()
        return p

    # ---------------- 内部: 搜索取卡片 ----------------
    def _search(self, keyword, pg='1', order='', duration='0'):
        self._ensure()
        params = {'search_type': 'video', 'keyword': keyword, 'page': int(pg)}
        if order and order != '0':
            params['order'] = order
        if duration and duration != '0':
            params['duration'] = duration
        try:
            signed = self._sign(params)
            url = API_SEARCH + '?' + urlencode(signed)
            r = self.session.get(url, timeout=15).json()
            result = (r.get('data') or {}).get('result') or []
        except Exception:
            result = []
        vods = []
        for it in result:
            bvid = it.get('bvid')
            if not bvid:
                continue
            title = re.sub(r'<[^>]+>', '', it.get('title', '') or '')
            pic = it.get('pic', '') or ''
            if pic.startswith('//'):
                pic = 'https:' + pic
            if pic:
                pic = pic + '@Referer=' + REFERER
            dur = it.get('duration', '') or ''
            author = it.get('author', '') or ''
            vods.append({
                'vod_id': bvid,
                'vod_name': title,
                'vod_pic': pic,
                'vod_remarks': (dur + ' ' + author).strip(),
            })
        return vods

    # ---------------- 首页分类 ----------------
    def homeContent(self, filter):
        cats = [
            ('影视解说', '影视解说'), ('电视剧', '电视剧'), ('纪录片', '纪录片'),
            ('音乐MV', '音乐MV'), ('舞蹈', '舞蹈'), ('美食', '美食'),
            ('儿童动画', '儿童动画'), ('健身', '健身'),
        ]
        classes = [{'type_name': n, 'type_id': i} for n, i in cats]
        base_filter = [
            {'key': 'order', 'name': '排序', 'value': [
                {'n': '综合', 'v': '0'}, {'n': '最多点击', 'v': 'click'},
                {'n': '最新发布', 'v': 'pubdate'}, {'n': '最多弹幕', 'v': 'dm'},
                {'n': '最多收藏', 'v': 'stow'}]},
            {'key': 'duration', 'name': '时长', 'value': [
                {'n': '全部', 'v': '0'}, {'n': '60分钟以上', 'v': '4'},
                {'n': '30~60分钟', 'v': '3'}, {'n': '10~30分钟', 'v': '2'},
                {'n': '10分钟以下', 'v': '1'}]},
        ]
        filters = {i: base_filter for _, i in cats}
        return {'class': classes, 'filters': filters}

    def homeVideoContent(self):
        return {'list': self._search('影视解说', '1')}

    # ---------------- 分类内容 ----------------
    def categoryContent(self, tid, pg, filter, extend):
        extend = extend or {}
        order = extend.get('order', '')
        duration = extend.get('duration', '0')
        keyword = extend.get('tid') or tid
        vods = self._search(keyword, pg, order, duration)
        return {'list': vods, 'page': int(pg), 'pagecount': 9999,
                'limit': 20, 'total': 999999}

    # ---------------- 详情(选集) ----------------
    def detailContent(self, ids):
        self._ensure()
        bvid = ids[0]
        # 元数据(标题/海报/简介)走 wbi/view, 能过风控; 失败则降级
        data = {}
        try:
            vp = self._sign({'bvid': bvid})
            r = self.session.get(API_VIEW + '?' + urlencode(vp), timeout=15).json()
            if r.get('code') == 0:
                data = r.get('data') or {}
        except Exception:
            data = {}
        title = data.get('title', bvid)
        pic = data.get('pic', '') or ''
        if pic:
            pic = pic + '@Referer=' + REFERER
        desc = data.get('desc', '') or ''
        # 年份/导演/演员 等元数据(显示在"站源"和"线路"之间)
        year = ''
        try:
            pd = data.get('pubdate') or data.get('ctime')
            if pd:
                year = time.strftime('%Y', time.localtime(int(pd)))
        except Exception:
            year = ''
        owner = (data.get('owner') or {}).get('name', '') or ''   # UP主=导演
        area = data.get('tname_v2') or data.get('tname') or ''    # 分区=地区
        staff = data.get('staff') or []                            # 联合投稿成员=演员
        actor = '、'.join(x.get('name', '') for x in staff if x.get('name')) or owner
        # 分P/cid 走 pagelist, 这个端点轻、稳、不易风控
        pages = []
        if data.get('pages'):
            pages = data['pages']
        else:
            try:
                pr = self.session.get(API_PAGELIST + '?bvid=' + bvid, timeout=15).json()
                pages = pr.get('data') or []
            except Exception:
                pages = []
        # 组装分P列表 (part名, cid)
        plist = []
        if len(pages) <= 1:
            cid = data.get('cid') or (pages[0].get('cid') if pages else '')
            plist.append((title, cid))
        else:
            for p in pages:
                part = p.get('part') or ('P' + str(p.get('page')))
                plist.append((part, p.get('cid')))
        # 探第一个分P有哪些清晰度(H.264) → 每档做成一条"线路"; 探不到就给默认档
        quals = self._avail_quals(bvid, plist[0][1]) if plist else []
        if not quals:
            quals = [('1080P', 80), ('720P', 64), ('480P', 32)]
        froms, urls = [], []
        for label, qn in quals:
            froms.append(label)
            eps = ['%s$%s|%s|%d' % (self._clean(nm), bvid, cid, qn) for nm, cid in plist]
            urls.append('#'.join(eps))
        vod = {
            'vod_id': bvid,
            'vod_name': title,
            'vod_pic': pic,
            'vod_year': year,
            'vod_area': area,
            'vod_director': owner,
            'vod_actor': actor,
            'vod_content': desc,
            'vod_play_from': '$$$'.join(froms),
            'vod_play_url': '$$$'.join(urls),
        }
        return {'list': [vod]}

    @staticmethod
    def _clean(s):
        return str(s or '').replace('$', ' ').replace('#', ' ').strip()

    # 探某 cid 有哪些 H.264 清晰度, 返回 [(线路名, qn), ...] 高→低
    def _avail_quals(self, bvid, cid):
        try:
            params = {'bvid': bvid, 'cid': cid, 'qn': '116', 'fnval': '4048', 'fourk': '1'}
            r = self.session.get(API_PLAYURL + '?' + urlencode(self._sign(params)), timeout=15).json()
            vids = ((r.get('data') or {}).get('dash') or {}).get('video') or []
            ids = sorted({x.get('id', 0) for x in vids if self._is_avc(x)}, reverse=True)
            return [(QUAL_LABEL.get(q, str(q)), q) for q in ids if q in QUAL_LABEL]
        except Exception:
            return []

    # ---------------- 搜索 ----------------
    def searchContent(self, key, quick, pg='1'):
        return {'list': self._search(key, pg), 'page': int(pg)}

    # ---------------- 播放(取真实地址) ----------------
    # 优先 dash(1080P+, 拼 MPD 经本地代理吐给播放器); 失败自动回退 mp4 单链(720P)。
    def playerContent(self, flag, id, vipFlags):
        self._ensure()
        parts = id.split('|')
        if len(parts) < 2:
            return {'parse': 0, 'url': '', 'header': {}}
        bvid, cid = parts[0], parts[1]
        qn = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 80
        hdr = {'User-Agent': UA, 'Referer': REFERER}
        # 1) dash: 按线路选中的清晰度 qn
        try:
            res = self._dash_play(bvid, cid, qn, hdr)
            if res:
                return res
        except Exception:
            pass
        # 2) 回退 mp4 720P
        return self._mp4_play(bvid, cid, hdr)

    @staticmethod
    def _is_avc(x):
        # 只认 H.264: codecid 7, 或 codecs 以 avc 开头(排除 hev1/hvc1/av01)
        return x.get('codecid') == 7 or str(x.get('codecs', '')).lower().startswith('avc')

    def _dash_play(self, bvid, cid, qn, hdr):
        params = {'bvid': bvid, 'cid': cid, 'qn': str(qn),
                  'fnval': '4048', 'fourk': '1'}
        r = self.session.get(API_PLAYURL + '?' + urlencode(self._sign(params)), timeout=15).json()
        data = r.get('data') or {}
        dash = data.get('dash') or {}
        videos = dash.get('video') or []
        audios = dash.get('audio') or []
        if not videos or not audios:
            return None
        # 只留 H.264, 每个清晰度留码率最高的一条
        best = {}
        for x in videos:
            if not self._is_avc(x):
                continue
            q = x.get('id', 0)
            if q not in best or x.get('bandwidth', 0) > best[q].get('bandwidth', 0):
                best[q] = x
        if not best:   # 极少数视频无 avc → 退回画质最高的任意编码
            top = sorted(videos, key=lambda x: (x.get('id', 0), x.get('bandwidth', 0)))[-1]
            best = {top.get('id', 0): top}
        # 选中线路对应的清晰度: 命中 qn 就用它, 否则取 ≤qn 的最高, 再否则取最低
        if qn in best:
            v = best[qn]
        else:
            le = [q for q in best if q <= qn]
            v = best[max(le)] if le else best[min(best)]
        vlist = [v]
        # 音频: 优先 AAC(mp4a, 兼容性最好), 取码率最高
        aacs = [x for x in audios if str(x.get('codecs', '')).lower().startswith('mp4a')]
        a = sorted(aacs or audios, key=lambda x: x.get('bandwidth', 0))[-1]
        dur = int((data.get('timelength') or 0)) / 1000.0
        mpd = self._build_mpd(vlist, a, dur)
        if not mpd:
            return None
        token = '%s_%s_%d' % (bvid, cid, int(time.time() * 1000))
        # 控制缓存体积: 只留最近 20 条
        if len(self._mpd_cache) > 20:
            for k in list(self._mpd_cache)[:-19]:
                self._mpd_cache.pop(k, None)
        self._mpd_cache[token] = mpd
        url = self.getProxyUrl() + '&type=mpd&k=' + token
        return {'parse': 0, 'url': url, 'format': 'application/dash+xml', 'header': hdr}

    def _mp4_play(self, bvid, cid, hdr):
        params = {'bvid': bvid, 'cid': cid, 'qn': '80',
                  'fnval': '1', 'fourk': '1', 'platform': 'html5'}
        play = ''
        try:
            r = self.session.get(API_PLAYURL + '?' + urlencode(self._sign(params)), timeout=15).json()
            data = r.get('data') or {}
            durl = data.get('durl') or []
            if durl:
                play = durl[0].get('url', '')
        except Exception:
            play = ''
        return {'parse': 0, 'url': play, 'header': hdr}

    @staticmethod
    def _seg(node):
        sb = node.get('segment_base') or node.get('SegmentBase') or {}
        return (sb.get('initialization', '0-0'), sb.get('index_range', '0-0'))

    def _build_mpd(self, vlist, a, dur):
        def esc(u):
            return (u or '').replace('&', '&amp;')
        aurl = esc(a.get('baseUrl') or a.get('base_url') or '')
        if not vlist or not aurl:
            return ''
        if dur <= 0:
            dur = 9999
        # 多条视频 Representation(各清晰度) 放同一 AdaptationSet → 播放器"画质"菜单可选 + 自适应
        vreps = []
        for i, v in enumerate(vlist):
            vurl = esc(v.get('baseUrl') or v.get('base_url') or '')
            if not vurl:
                continue
            v_init, v_idx = self._seg(v)
            vreps.append(
                '   <Representation id="v%d" bandwidth="%d" codecs="%s" width="%d" height="%d" frameRate="%s">\n'
                '    <BaseURL>%s</BaseURL>\n'
                '    <SegmentBase indexRange="%s"><Initialization range="%s"/></SegmentBase>\n'
                '   </Representation>\n' % (
                    i, int(v.get('bandwidth', 0) or 0),
                    v.get('codecs', 'avc1.640028') or 'avc1.640028',
                    int(v.get('width', 0) or 0), int(v.get('height', 0) or 0),
                    str(v.get('frameRate') or v.get('frame_rate') or '25'),
                    vurl, v_idx, v_init))
        if not vreps:
            return ''
        a_init, a_idx = self._seg(a)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
            'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
            'type="static" mediaPresentationDuration="PT%.1fS" minBufferTime="PT1.5S">\n'
            ' <Period duration="PT%.1fS">\n'
            '  <AdaptationSet contentType="video" mimeType="video/mp4" segmentAlignment="true" startWithSAP="1">\n'
            '%s'
            '  </AdaptationSet>\n'
            '  <AdaptationSet contentType="audio" mimeType="audio/mp4" segmentAlignment="true" startWithSAP="1">\n'
            '   <Representation id="a" bandwidth="%d" codecs="%s">\n'
            '    <BaseURL>%s</BaseURL>\n'
            '    <SegmentBase indexRange="%s"><Initialization range="%s"/></SegmentBase>\n'
            '   </Representation>\n'
            '  </AdaptationSet>\n'
            ' </Period>\n'
            '</MPD>\n'
        ) % (
            dur, dur, ''.join(vreps),
            int(a.get('bandwidth', 0) or 0), a.get('codecs', 'mp4a.40.2') or 'mp4a.40.2',
            aurl, a_idx, a_init,
        )

    def localProxy(self, param):
        # dash: 把拼好的 MPD 吐给播放器。返回 [状态码, mime, 字节, 头]
        try:
            p = param if isinstance(param, dict) else json.loads(param or '{}')
            if p.get('type') == 'mpd':
                mpd = self._mpd_cache.get(p.get('k'))
                if mpd:
                    return [200, 'application/dash+xml', mpd.encode('utf-8'), {}]
                return [404, 'text/plain', b'', {}]
        except Exception as e:
            return [500, 'text/plain', str(e).encode('utf-8'), {}]
        return [404, 'text/plain', b'', {}]
