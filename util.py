# -*- coding: UTF-8 -*-
from typing import List, Set
from sqlitedict import SqliteDict
from nonebot import *
import asyncio
import base64
import io
import math
import requests
import imghdr
import uuid
import yaml
import json
import os
import re

bot = get_bot()

# 题图下载上限与超时：(连接超时, 读取超时)
IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_TIMEOUT = (10, 20)

# 问答列表渲染成图片的相关设置
LIST_RENDER_AS_IMAGE = True      # 关掉就退回纯文本
LIST_IMAGE_MAX_CHARS = 200       # 文本超过这个长度才渲染成图，很短的列表仍然直接发文字
LIST_IMAGE_WIDTH = 900
LIST_IMAGE_PADDING = 28
LIST_CELL_MIN_WIDTH = 150        # 每列最小宽度，用来决定列数（900px 下得到 5 列）
LIST_CELL_GAP = 6                # 单元格左右留白，长词跨列时按这个算格数
LIST_FONT_PATH = r'C:\Windows\Fonts\msyh.ttc'
LIST_FONT_PATH_BOLD = r'C:\Windows\Fonts\msyhbd.ttc'


# 获取配置
def get_config():
    file = open(os.path.join(os.path.dirname(__file__), "config.yaml"), 'r', encoding="utf-8")
    return yaml.load(file.read(), Loader=yaml.FullLoader)


config = get_config()


# 获取字符串中的关键字  is_first为true返回后面 false返回 前面和后面
def get_msg_keyword(keyword, msg, is_first=False):
    try:
        res = re.split(format_reg(keyword, is_first), msg, 1)
        res = tuple(res[::-1]) if len(res) == 2 else False
    except TypeError:
        return False
    return ''.join(res) if is_first and res else res


# 格式化配置中的正则表达式
def format_reg(keyword, is_first=False):
    keyword = keyword if isinstance(keyword, list) else [keyword]
    return f"{'|'.join([f'^{i}' for i in keyword] if is_first else keyword)}"


def get_path(*paths):
    return os.path.join(os.path.dirname(__file__), *paths)


# 初始化数据库
def init_db(db_dir, db_name='db.sqlite', tablename=''):
    return SqliteDict(get_path(db_dir, db_name),
                      tablename=tablename,
                      encode=json.dumps,
                      decode=json.loads,
                      autocommit=True)


# 寻找MessageSegment里的某个关键字的位置
def find_ms_str_index(ms, keyword, is_first=False):
    for index, item in enumerate(ms):
        if item['type'] == 'text' and re.search(format_reg(keyword, is_first), item['data']['text']):
            return index
    return -1


# 按文件头识别图片格式，认不出返回 None
# imghdr 已废弃且只认几种格式：渐进式 JPEG、Adobe 变体 JPEG 都会被误判成 None，
# 结果就是缓存里那批 .None 文件。这里补上对字节流的直接判断。
def sniff_image_ext(content):
    if not content:
        return None
    if content[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    if content[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if content[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return 'webp'
    if content[:2] == b'BM':
        return 'bmp'
    if content[:4] in (b'II*\x00', b'MM\x00*'):
        return 'tiff'
    return imghdr.what(None, content)


# 下载图片到本地 并返回message图片类型
async def ms_handler_image(ms, msg_diy=False, cache_dir='', dir_name='img', b64=False):
    url = str(ms['data']['url'] if ms['data'].get('url') else ms['data']['file']).strip()
    if not url:
        return False
    if msg_diy and url[0] == '?':
        return MessageSegment.image(url[1:])
    try:
        # 放到线程池里跑，避免阻塞事件循环（否则设图期间整个 bot 卡住最长 30 秒）
        pic = await asyncio.get_event_loop().run_in_executor(
            None, lambda: requests.get(url, timeout=IMAGE_TIMEOUT, stream=True))
    except Exception as e:
        print(f'[eqa] 下载题图失败 {url[:100]}: {e!r}')
        return False
    # 任何 HTTP 错误都算失败，否则会把这个错误页当图片存下来
    if pic.status_code != 200:
        print(f'[eqa] 下载题图失败 {url[:100]}: HTTP {pic.status_code}')
        pic.close()
        return False
    try:
        chunks = []
        size = 0
        for chunk in pic.iter_content(64 * 1024):
            size += len(chunk)
            if size > IMAGE_MAX_BYTES:
                print(f'[eqa] 题图超过 {IMAGE_MAX_BYTES} 字节上限，已放弃 {url[:100]}')
                return False
            chunks.append(chunk)
        content = b''.join(chunks)
    except Exception as e:
        print(f'[eqa] 读取题图失败 {url[:100]}: {e!r}')
        return False
    finally:
        pic.close()
    if not content:
        print(f'[eqa] 题图内容为空 {url[:100]}')
        return False
    ext = sniff_image_ext(content) or 'img'
    base64_suffix = '.base64' if b64 else ''
    img_dir = get_path(cache_dir, dir_name)
    os.makedirs(img_dir, exist_ok=True)
    file_name = os.path.join(img_dir, f'{uuid.uuid1().hex}.{ext}{base64_suffix}')
    payload = bytes(pic2b64(content), encoding="utf8") if b64 else content
    with open(file_name, 'wb') as fp:
        fp.write(payload)
    protocol = '' if b64 else 'file:///'
    return MessageSegment.image(f'{protocol}{os.path.abspath(file_name)}')


# 是否是群管理员
def is_group_admin(ctx):
    return ctx['sender']['role'] in ['owner', 'admin', 'administrator']


def filter_list(plist, func):
    return list(filter(func, plist))


# 获取群内的群友名字
async def get_group_member_name(group_id, user_id):
    qq_info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
    return qq_info['card'] or qq_info['nickname']


# 获取当前群设置的问题列表
def get_current_ans_list(ctx, ans_list):
    return filter_list(ans_list,
                       lambda x: (x[0]['group_id'] if isinstance(x, list) else x['group_id']) == ctx['group_id'])


def get_all_ans_list_by_qq(qq, ans_list):
    return filter_list(ans_list, lambda x: filter_list(x, lambda w: w['user_id'] == qq)
    if isinstance(x, list) else x['user_id'] == qq)


def get_qus_str_by_list(ans_list, reg_keys=None):
    """把记录列表转成用于展示的问题名集合。
    reg_keys 传入正则表的键集合；老记录没有 display 字段时，靠它补一个 R 前缀，
    避免用户看到一串看不懂的正则却不知道那是自己设的模式。"""
    out = set()
    for i in ans_list:
        r0 = i[0] if isinstance(i, list) else i
        name = r0.get('display')
        if not name:
            name = r0['qus']
            if reg_keys and r0['qus'] in reg_keys:
                name = 'R' + name
        out.add(truncate_question(name))
    return out


# 正则元字符：出现任何一个才算“用户真的在写正则”
_REGEX_META = set('.^$*+?{}[]\\|()')


def has_regex_meta(s):
    return any(ch in _REGEX_META for ch in str(s))


def split_multi_keyword(s):
    """把 A|B|C 拆成关键词列表，过滤空项；少于两个有效词就不算多关键词写法"""
    parts = [p.strip() for p in str(s).split('|')]
    parts = [p for p in parts if p]
    return parts if len(parts) >= 2 else []


def build_multi_keyword_regex(s):
    """把 A|B|C 变成一个安全的正则：每段都转义，整体用非捕获组包起来，
    这样 C++ / C# 这类含元字符的关键词也能按字面匹配"""
    parts = split_multi_keyword(s)
    return '(?:%s)' % '|'.join(re.escape(p) for p in parts)


# 展示时截断超长问题：有人把整段文字当问题设置过（现存最长 2311 字符），
# 「全部问答」会把这一整段打出来，撑爆消息长度
QUESTION_DISPLAY_MAX = 60


def truncate_question(qus, limit=QUESTION_DISPLAY_MAX):
    qus = str(qus)
    if len(qus) <= limit:
        return qus
    # 含 CQ 码的不截断，切断了会把 @xxx 变成残缺片段，显示反而更乱
    if '[CQ:' in qus:
        head = qus[:limit]
        if '[CQ:' not in head:
            return head + '…'
        return qus
    return qus[:limit] + '…'


def pic2b64(pic) -> str:
    return 'base64://' + base64.b64encode(pic).decode()


def get_file_suffix(file_name: str) -> str:
    return os.path.splitext(file_name)[-1]


def message_image2base64(message):
    # SqliteDict 返回的 message 是数据库对象；转换发送格式时不能原地修改它。
    message = [dict(value, data=dict(value['data'])) for value in message]
    for index, value in enumerate(message):
        if value['type'] != 'image':
            continue
        url = value['data'].get('file') or value['data'].get('url') or ''
        if get_file_suffix(url) != '.base64':
            continue
        path = url[8:] if url.startswith('file:///') else url
        try:
            with open(path, encoding='utf8') as f:
                message[index] = MessageSegment.image(f.read())
        except OSError as e:
            print(f'[eqa] 设置的图片无法读取: {e}')
    return message


def delete_message_image_file(message):
    message = message if isinstance(message, list) else [message]
    image_dir = os.path.realpath(get_path(config['cache_dir'], 'img'))
    for value in message:
        for segment in filter_list(value['message'], lambda x: x['type'] == 'image'):
            raw = str(segment['data'].get('file') or segment['data'].get('url') or '')
            if raw.startswith(('http://', 'https://', 'base64://', '?')):
                continue
            if raw.startswith('file:///'):
                raw = raw[8:]
            path = os.path.realpath(raw)
            try:
                if os.path.commonpath((image_dir, path)) != image_dir:
                    continue
            except ValueError:
                continue
            try:
                os.remove(path)
            except OSError as e:
                print(f'[eqa] 缓存图片删除失败: {e}')


# 获取消息中字符串 处理md5值
def get_message_str(message, is_reg=False):
    res = ''
    message = message if isinstance(message, MessageSegment) else Message(message)
    for ms in message:
        # 处理文本
        if ms['type'] == 'text':
            res += str(ms['data']['text']).strip()
            if res == config['str']['reg_match_cmd']:
                is_reg = True
            continue
        # 处理图片
        if ms['type'] == 'image':
            file = ms['data']['file']
            try:
                _id = re.match('{.+}', file).group()[1:-1]
                res += _id.split('-')[-1]
            except AttributeError:
                res += ms['data']['file'].split('.')[0].lower()
            continue
        # AT
        if is_reg and ms['type'] == 'at':
            res += 'at%s' % ms['data']['qq']
            continue
        res += str(ms)
    return res


# 把cq码转换成字符串
async def cq_msg2str(msg: List[str] or Set[str], group_id=None):
    msg = list(msg) if isinstance(msg, set) else msg
    # 取名失败的条目先记下来，循环结束后统一删
    # 原来在 enumerate 里直接 pop，后面的元素会前移导致索引错位、漏项
    drop = []
    for index, value in enumerate(msg):
        at = re.match('\[CQ:at,qq=(\d+)', value)
        if at and group_id:
            qq = int(at.group(1))
            try:
                name = await get_group_member_name(group_id, qq)
                msg[index] = f'@{name}'
            except Exception:
                drop.append(index)
    for index in reversed(drop):
        msg.pop(index)
    return msg


# ---------------- 问答列表渲染成图片 ----------------
# 长列表直接发文字会刷屏（最大的群 157 条、一千多字符），渲染成图只占一屏

def _load_font(size, bold=False):
    from PIL import ImageFont
    path = LIST_FONT_PATH_BOLD if bold else LIST_FONT_PATH
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        # 字体缺失时退回默认字体，宁可排版难看也不要让功能挂掉
        return ImageFont.load_default()


def _split_by_width(s, font, measure, max_w):
    """把过长的字符串按像素宽度硬切成若干段"""
    pieces = []
    cur = ''
    for ch in s:
        if cur and measure(cur + ch, font) > max_w:
            pieces.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        pieces.append(cur)
    return pieces


def render_list_image(title, blocks, footer=''):
    """
    title:  顶部标题
    blocks: [{'name': 分区名, 'note': 说明, 'items': ['词1', '词2*', ...]}, ...]
    footer: 底部提示文字
    返回生成的文件路径，失败返回 None
    """
    try:
        from PIL import Image, ImageDraw
    except Exception as e:
        print(f'[eqa] 渲染列表需要 Pillow: {e!r}')
        return None

    W = LIST_IMAGE_WIDTH
    PAD = LIST_IMAGE_PADDING
    f_title = _load_font(30, bold=True)
    f_section = _load_font(23, bold=True)
    f_note = _load_font(17)
    f_item = _load_font(20)
    f_foot = _load_font(17)

    C_BG = (250, 250, 252)
    C_TITLE = (32, 32, 38)
    C_SECTION = (44, 84, 160)
    C_NOTE = (128, 128, 138)
    C_ITEM = (48, 48, 56)
    C_FOOT = (110, 110, 120)
    C_LINE = (222, 224, 230)

    # 用临时画布测量，因此要先算总高度，再真正绘制
    tmp = Image.new('RGB', (W, 10))
    dm = ImageDraw.Draw(tmp)

    def text_w(s, font):
        b = dm.textbbox((0, 0), s, font=font)
        return b[2] - b[0]

    item_h = 32
    inner_w = W - PAD * 2
    # 表格列数：按画布宽度定，900px 下 5 列每列约 168px，能装下绝大多数问题
    cols = max(1, inner_w // LIST_CELL_MIN_WIDTH)
    cell_w = inner_w / cols
    C_GRID = (233, 236, 242)
    C_CELL_BG = (255, 255, 255)

    # 先做一遍排版计算，得到每行的内容与总高度
    layout = []
    y = PAD
    layout.append(('text', title, f_title, C_TITLE, y))
    y += 46
    layout.append(('line', None, None, C_LINE, y))
    y += 20

    for blk in blocks:
        name = blk.get('name') or ''
        note = blk.get('note') or ''
        items = blk.get('items') or []
        sep_line = f'{name} · {len(items)} 条' if items else f'{name} · 暂无'
        layout.append(('text', sep_line, f_section, C_SECTION, y))
        y += 34
        if note:
            layout.append(('text', note, f_note, C_NOTE, y))
            y += 26

        if items:
            # 排成网格：每个词占 1 格，放不下的长词跨多格，整行放满才换行
            rows_grid = []
            cur, used = [], 0
            for it in items:
                bw = text_w(it, f_item)
                avail = cell_w - LIST_CELL_GAP
                span = max(1, int(math.ceil(bw / avail)))
                # 比整行还宽的词（截断后仍有 60 字的怪问题）：独占整行并硬折成多行，
                # 否则跨列数会被 cols 截断，文字直接画到画布外面
                if bw > inner_w - LIST_CELL_GAP:
                    if cur:
                        rows_grid.append(cur)
                        cur, used = [], 0
                    for piece in _split_by_width(it, f_item, text_w, inner_w - LIST_CELL_GAP):
                        rows_grid.append([(piece, cols)])
                    continue
                span = min(span, cols)
                if used + span > cols:
                    rows_grid.append(cur)
                    cur, used = [], 0
                cur.append((it, span))
                used += span
                if used >= cols:
                    rows_grid.append(cur)
                    cur, used = [], 0
            if cur:
                rows_grid.append(cur)

            grid_top = y
            layout.append(('grid', (rows_grid, grid_top, cell_w, item_h), None, None, y))
            y += len(rows_grid) * item_h
            layout.append(('gridlines', (rows_grid, grid_top, cell_w, item_h), None, C_GRID, y))

        y += 18
        layout.append(('line', None, None, C_LINE, y))
        y += 20

    if footer:
        layout.append(('text', footer, f_foot, C_FOOT, y))
        y += 30
    H = y + PAD

    img = Image.new('RGB', (W, H), C_BG)
    d = ImageDraw.Draw(img)
    # 网格线必须最先画，否则会压在跨格文字上面
    for kind, content, font, color, yy in layout:
        if kind == 'gridlines':
            rows_grid, grid_top, cw, ih = content
            gh = len(rows_grid) * ih
            for ri in range(len(rows_grid) + 1):
                gy = grid_top + ri * ih
                d.line([(PAD, gy), (W - PAD, gy)], fill=color, width=1)
            for ci in range(cols + 1):
                gx = PAD + ci * cw
                d.line([(gx, grid_top), (gx, grid_top + gh)], fill=color, width=1)
    for kind, content, font, color, yy in layout:
        if kind == 'text':
            d.text((PAD, yy), content, font=font, fill=color)
        elif kind == 'line':
            d.line([(PAD, yy), (W - PAD, yy)], fill=color, width=1)
        elif kind == 'grid':
            rows_grid, grid_top, cw, ih = content
            for ri, row in enumerate(rows_grid):
                x = PAD
                ry = grid_top + ri * ih
                for it, span in row:
                    # 跨格单元格内补一条白底，把底下的竖线盖掉，文字才不会被切
                    d.rectangle([x + 1, ry + 1, x + span * cw - 1, ry + ih - 1], fill=C_CELL_BG)
                    d.text((x + 6, ry + (ih - 24) // 2), it, font=f_item, fill=C_ITEM)
                    x += span * cw

    img_dir = get_path(config['cache_dir'], 'list')
    os.makedirs(img_dir, exist_ok=True)
    out_path = os.path.join(img_dir, f'list_{uuid.uuid1().hex}.png')
    img.save(out_path)
    return out_path


def cleanup_list_images(keep=20):
    """列表图是临时产物，只保留最近 keep 张，避免长期累积"""
    try:
        d = get_path(config['cache_dir'], 'list')
        if not os.path.isdir(d):
            return
        files = [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith('.png')]
        if len(files) <= keep:
            return
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for p in files[keep:]:
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception as e:
        print(f'[eqa] 清理列表图失败: {e!r}')
