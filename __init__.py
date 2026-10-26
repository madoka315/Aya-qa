# -*- coding: UTF-8 -*-
"""
作者艾琳有栖

版本 0.1.0

基于 nonebot 问答
"""
import os
import re
import random
from pathlib import Path
from nonebot import *
from . import util

from hoshino import Service  # 如果使用hoshino的分群管理取消注释这行

#
sv = Service('问答')  # 如果使用hoshino的分群管理取消注释这行

config = util.get_config()
db = util.init_db(config['cache_dir'], tablename='unnamed')
reg_db = util.init_db(config['cache_dir'], tablename='reg')

_bot = get_bot()

admins = config['admins']
admins = set((admins if isinstance(admins, list) else [admins]) + _bot.config.SUPERUSERS)


@sv.on_message('group')  # 如果使用hoshino的分群管理取消注释这行 并注释下一行的 @_bot.on_message("group")
# @_bot.on_message("group") # nonebot使用这
async def eqa_main(*params):
    bot, ctx = (_bot, params[0]) if len(params) == 1 else params

    msg = str(ctx['message']).strip()

    # 显示全部设置的问题
    show_target = util.get_msg_keyword(config['comm']['show_question_list'], msg, True)
    if isinstance(show_target, str):
        # 命令与关键词之间的空白仅作分隔符，不参与搜索
        show_target = show_target.strip()
        return await bot.send(ctx, await build_question_list(ctx, show_target))

    # 显示设置的问题
    show_target = util.get_msg_keyword(config['comm']['show_question'], msg, True)
    if isinstance(show_target, str):
        return await bot.send(ctx, await show_question(ctx, show_target))

    # 删除设置的问题
    del_target = util.get_msg_keyword(config['comm']['answer_delete'], msg, True)
    if del_target:
        return await bot.send(ctx, await del_question(ctx, del_target))

    # 清空设置的问题
    del_all = util.get_msg_keyword(config['comm']['answer_delete_all'], msg, True)
    if del_all:
        return await bot.send(ctx, await del_question(ctx, del_all, True))

    # 更换回答xxx 内容：问题只有一条回答时直接替换回答内容
    # 没匹配到已登记的问题时返回 None，继续走下面的原有流程
    replace_res = await replace_answer(ctx)
    if replace_res is not None:
        return await bot.send(ctx, replace_res)

    # 处理回答所有人的问题
    keyword = util.get_msg_keyword(config['comm']['answer_all'], msg, True)
    if keyword:
        msg = await ask(ctx, keyword, False)
        if msg:
            return await bot.send(ctx, msg)

    # 处理回答自己的问题
    keyword = util.get_msg_keyword(config['comm']['answer_me'], msg, True)
    if keyword:
        msg = await ask(ctx, keyword, True)
        if msg:
            return await bot.send(ctx, msg)

    # 回复消息
    ans = await answer(ctx)
    if isinstance(ans, list):
        return await bot.send(ctx, ans)
    # elif isinstance(ans, str):
    #     return ans


# 设置问题的函数
async def ask(ctx, keyword, is_me):
    is_super_admin = ctx['user_id'] in admins
    is_admin = util.is_group_admin(ctx) or is_super_admin

    if config['rule']['only_admin_answer_all'] and not is_me and not is_admin:
        return '回答所有人的只能管理设置啦'

    question_handler = config['comm']['answer_me'] if is_me else config['comm']['answer_all']
    answer_handler = config['comm']['answer_handler']
    qa_msg = util.get_msg_keyword(answer_handler, keyword)
    if not qa_msg:
        # return f'嗯嗯，加上{answer_handler}后再重新设置试试看吧~'
        return False
    ans, qus = qa_msg
    qus = f'{qus}'.strip().replace('&#91;', '[').replace('&#93;', ']')
    if not str(qus).strip():
        return '问题呢? 问题呢??'
    if not str(ans).strip():
        return '回答呢? 回答呢??'

    # 问题与回答的分割
    ans_start = util.find_ms_str_index(ctx['message'], answer_handler)
    # 找不到分界说明「回答」二字被协议端拆进了不同消息段（或本就不在消息段开头），
    # 此时切片会整体错位，直接放弃这次设置，避免存进错乱的问答
    if ans_start < 0:
        return '没找到「回答」分隔符呢，试试把问题和回答放在一条消息里~'

    if re.search(r'\[CQ:image,', qus):
        qus = util.get_message_str(ctx['message'][:ans_start])
        qus = util.get_msg_keyword(question_handler, qus, True).strip()

    message = []
    _once = False
    for ms in ctx['message'][ans_start:]:
        if ms['type'] == 'text':
            reg = util.get_msg_keyword(answer_handler, ms['data']['text'])
            if reg and not _once:
                _once = True
                ms = MessageSegment.text(reg[0])
        if ms['type'] == 'image':
            ms = await util.ms_handler_image(ms, config['rule']['use_cq_code_image_url'],
                                             config['cache_dir'], b64=config['image_base64'])
            if not ms:
                return '图片缓存失败了啦！'
        message.append(ms)

    # 判断是否是正则表达式的问答
    # 只有「R 后面确实含正则元字符」才当正则，否则 R 只是问题的一部分。
    # 原来是无条件按 R 开头截断，导致「我说RPG回答x」被静默存成正则「PG」。
    reg_qus = util.get_msg_keyword(config['str']['reg_match_cmd'], qus, True)
    multi_kw = util.split_multi_keyword(qus)
    if reg_qus and util.has_regex_meta(reg_qus):
        try:
            re.compile(reg_qus)
        except Exception:
            return '设置的正则表达式不对啦'
        if re.search(r'\[CQ:at,', qus):
            reg_qus = util.get_message_str(reg_qus, True)

        _db = reg_db  # 切换为正则表达式的数据库
        qus = reg_qus
        display = 'R' + qus  # 展示时带上 R，方便用户知道这是正则
    elif multi_kw:
        # A|B|C 这种多关键词写法，转成安全的正则存进正则表，复用同一套匹配
        display = qus  # 展示时保持用户原来的写法，不露出内部构造的正则
        qus = util.build_multi_keyword_regex(qus)
        _db = reg_db
    else:
        _db = db  # 否则使用普通的数据库
        display = qus

    qus_list = _db.get(qus, [])
    qus_list.append({
        'user_id': ctx['user_id'],
        'group_id': ctx['group_id'],
        'is_me': is_me,
        'qus': qus,
        'display': display,
        'message': message
    })
    _db[qus] = qus_list
    return '我学会啦 来问问我吧！'


# 回复的函数
async def answer(ctx, _reg_flag=False):
    msg = util.get_message_str(ctx['message']).strip()
    ans_list = db.get(msg, [])
    _is_reg = False
    if not ans_list or _reg_flag:
        msg = str(ctx['message']).strip()
        reg_msg = util.get_message_str(msg, True)
        reg_list = list(filter(None, map(lambda x: re.search(x, reg_msg) and reg_db[x] or None, reg_db.keys())))
        if not reg_list:
            return False
        ans_list = sum(reg_list, [])
        _is_reg = True

    group_id = ctx['group_id']
    user_id = ctx['user_id']
    super_admin_is_all_group = config['rule']['super_admin_is_all_group']
    priority_self_answer = config['rule']['priority_self_answer']
    multiple_question_random_answer = config['rule']['multiple_question_random_answer']

    # 获取到当前群的列表 判断是否来自该群 或者是否是超级管理员
    # 超级管理员设置的是否为所有群问答
    ans_list = util.filter_list(ans_list, lambda x: group_id == x['group_id'] or (
        x['user_id'] in admins if super_admin_is_all_group else False))

    # 木有在这群
    if not ans_list and _reg_flag:
        return False
    elif not ans_list:
        return await answer(ctx, True)

    # 先把「别人的个人回答」筛掉，再做优先级和随机
    # 原来的顺序是先随机再检查 is_me，选中别人的个人回答时直接 return False，
    # 哪怕列表里本来就有可用的全体回答，触发者也会收到空气
    ans_list = util.filter_list(ans_list, lambda x: (not x['is_me']) or x['user_id'] == user_id)
    if not ans_list:
        return False

    # 是否优先自己的回答 是的话则选择自己的列表
    if priority_self_answer:
        self_list = util.filter_list(ans_list, lambda x: user_id == x['user_id'])
        ans_list = self_list if self_list else ans_list

    # 判断规则是否随机
    if multiple_question_random_answer:
        # 随机选个
        ans = random.choice(ans_list)
    else:
        # 否则选最后一个
        ans = ans_list[-1]

    ans_msg = ans['message']
    _msg = ans_msg[0]
    if len(ans_msg) == 1:  # str(Message(msg))
        if _msg['type'] == 'text' and _msg['data']['text'][:1] == config['str']['cmd_head_str']:
            ctx['raw_message'] = _msg['data']['text'][1:]
            ctx['message'] = Message(ctx['raw_message'])
            ctx['to_me'] = True
            _bot.on_message(ctx)
            return False
    if _is_reg and _msg['type'] == 'text' and _msg['data']['text'][:1] == config['str']['reg_match_cmd']:
        text = _msg['data']['text'][1:] or str(Message(ans_msg[1:]))
        ans_msg = Message(re.sub(ans['qus'], text, msg))

    # 如果使用了base64 那么需要把信息里的图片转换一下
    if config['image_base64']:
        ans['message'] = util.message_image2base64(ans['message'])
    
    for value in ans['message']:
        if value['type'] == 'image':
            raw = str(value['data'].get('file') or '')
            # 自定义图源（use_cq_code_image_url 开启时以 ? 开头设置）不能被重写成本地路径，
            # 否则会被覆盖成不存在的缓存文件，图片必然发不出去。
            # 存进库里时前导 ? 已被剥掉，所以 http/https 也要一并跳过。
            if raw.startswith(('?', 'http://', 'https://', 'base64://')):
                continue
            # 处理一下绝对路径问题
            img_path = Path(raw)
            img_name = img_path.stem + img_path.suffix
            new_path = Path(__file__).parent / config['cache_dir'] / 'img' / img_name
            value['data']['file'] = 'file:///' + str(new_path)
            
    # 最后就是把验证成功的消息返回去
    return ans_msg


# 显示问题的函数
async def show_question(ctx, target, show_all=False):
    print_all_split = config['str']['print_all_split'] or " | "

    db_list = list(db.values()) + list(reg_db.values())

    # 获取当前群设置的问题列表
    ans_list = util.get_current_ans_list(ctx, db_list)

    # 「全部问答 关键词」的搜索词，去掉 @ 之后剩下的部分
    keyword = ''
    if show_all:
        keyword = re.sub(r'\[CQ:at,qq=\d+]', '', target).strip()

    if not show_all:
        # 如果只显示个人
        is_super_admin = ctx['user_id'] in admins
        is_admin = util.is_group_admin(ctx) or is_super_admin

        # 如果跟了@人的对象
        target = list(int(i) for i in re.findall(r'\[CQ:at,qq=(\d+)]', target.strip()))
        is_at = bool(target)

        # 如果关了群友查询别人的选项
        if not config['rule']['member_can_show_other'] and target and not is_admin:
            return '不能看别人设置的问题啦'

        # 如果跟着@人对象 就显示@人的  没有就显示自己的
        target = target if is_at else [ctx['user_id']]
    else:
        # 显示全部
        target = [ctx['user_id']]
        is_at = False

    # 查别人（带 @）时仍然是纯文字，一行一个人名，不需要复杂的图
    msg = ''
    for qq in target:
        head = ''
        priority_list = []
        if not show_all:  # 不是显示全部的话就筛选个人
            # 获取当前qq设置问题列表
            if qq in admins:
                ans_list = util.get_all_ans_list_by_qq(qq, db_list)
            else:
                ans_list = util.get_all_ans_list_by_qq(qq, ans_list)

        else:
            # 这是所有人的问答
            all_list = util.filter_list(ans_list, lambda x: True in list(not i['is_me'] for i in x))
            # 这人个人问答
            priority_list = util.filter_list(ans_list, lambda x: True in list(i['is_me'] for i in x))

            ans_list = sum(list(util.get_all_ans_list_by_qq(q, db_list) for q in admins), all_list)

        # 如果是多个人 那就加个名字区别一下
        if is_at:
            name = await util.get_group_member_name(ctx['group_id'], qq)
            head = f'{name} :\n'

        str_list = util.get_qus_str_by_list(ans_list, set(reg_db.keys()))
        str_list = await util.cq_msg2str(str_list, group_id=ctx['group_id'])
        str_list = sorted(str_list)
        # 把问题都打印出来
        msg_context = f'全体问答:\n{print_all_split.join(str_list)}' if show_all else "\n".join(str_list)

        priority_msg = ''
        if show_all:
            pri_str_list = util.get_qus_str_by_list(priority_list, set(reg_db.keys()))
            pri_str_list = await util.cq_msg2str(pri_str_list, group_id=ctx['group_id'])
            priority_msg = "\n个人问答:\n" + print_all_split.join(pri_str_list)

        msg = "{}{}{}{}\n".format(msg, head, msg_context if ans_list else f'还没有设置过问题呢', priority_msg)
    return msg


# 渲染「全部问答」：分区 + 重复标记 + 关键词搜索 + 过长时转图片
async def build_question_list(ctx, keyword=''):
    db_list = list(db.values()) + list(reg_db.values())
    ans_list = util.get_current_ans_list(ctx, db_list)

    # 全群问答：任何人设置的非个人问答，加上超管设置的全群共享
    all_list = util.filter_list(ans_list, lambda x: True in list(not i['is_me'] for i in x))
    all_list = sum(list(util.get_all_ans_list_by_qq(q, db_list) for q in admins), all_list)
    # 个人专属问答：设置者本人触发才有效
    pri_list = util.filter_list(ans_list, lambda x: True in list(i['is_me'] for i in x))

    reg_keys = set(reg_db.keys())

    def to_names(records):
        return util.get_qus_str_by_list(records, reg_keys)

    all_names = await util.cq_msg2str(to_names(all_list), group_id=ctx['group_id'])
    pri_names = await util.cq_msg2str(to_names(pri_list), group_id=ctx['group_id'])
    all_names = set(all_names)
    pri_names = set(pri_names)

    # 搜索：按关键词过滤，两个分区各过滤各的
    if keyword:
        kw = keyword.lower()
        all_names = set(n for n in all_names if kw in n.lower())
        pri_names = set(n for n in pri_names if kw in n.lower())
        if not all_names and not pri_names:
            return f'没有找到包含「{keyword}」的问答呢'

    # 同时出现在两个分区的词打个 * 标记，避免看起来像重复设置了两遍
    both = all_names & pri_names
    all_items = sorted((n + '*' if n in both else n) for n in all_names)
    pri_items = sorted((n + '*' if n in both else n) for n in pri_names)

    total = len(all_names) + len(pri_names) - len(both)

    def note_for(n_total):
        return '任何人都能触发，由超管或群管设置' if n_total else ''

    blocks = [
        {'name': '全群问答', 'note': note_for(len(all_names)), 'items': all_items},
        {'name': '个人专属问答', 'note': '只有设置者本人触发才有效' if pri_items else '',
         'items': pri_items},
    ]

    title = f'本群问答 {total} 条'
    if keyword:
        title += f'（搜索：{keyword}）'

    foot_lines = ['带 * 的词两个分区都有']
    foot_lines.append('查自己设的 → 问答     查某人设的 → 问答@某人')
    foot_lines.append('搜索 → 全部问答 关键词')
    footer = '    '.join(foot_lines)

    # 纯文本形态（同时用于长度判断和图片渲染失败时的兜底）
    print_all_split = config['str']['print_all_split'] or ' | '
    plain_parts = []
    for blk in blocks:
        if not blk['items']:
            continue
        plain_parts.append(f"{blk['name']}（{len(blk['items'])} 条）")
        if blk.get('note'):
            plain_parts.append(blk['note'])
        plain_parts.append(print_all_split.join(blk['items']))
    if not plain_parts:
        return f'本群还没有设置过问答呢' if not keyword else f'没有找到包含「{keyword}」的问答呢'
    plain = title + '\n' + '\n'.join(plain_parts)

    # 短的直接发文字，长的渲染成图片，避免刷屏
    if util.LIST_RENDER_AS_IMAGE and len(plain) > util.LIST_IMAGE_MAX_CHARS:
        path = util.render_list_image(title, blocks, footer)
        if path:
            util.cleanup_list_images()
            return MessageSegment.image('file:///' + os.path.abspath(path))
    return plain + '\n' + footer


# 删除问题的函数
async def del_question(ctx, target, clear=False):
    target = util.get_message_str(target).strip()

    # 定位要删的键：不让用户记住「正则要加 R」这件事。
    # 顺序是 普通表精确 -> 正则表（带 R 的剥掉 R 再试）-> 多关键词写法转正则再试，
    # 哪个先命中就用哪个，命中不了才提示没这个问题。
    _db, key = _locate_question_key(target)
    if _db is None:
        return '没这个问题哦'

    ans_list = _db.get(key, [])
    if not ans_list:
        return '没这个问题哦'

    is_super_admin = ctx['user_id'] in admins
    is_group_admin = util.is_group_admin(ctx) if config['rule']['only_admin_can_delete'] else True
    is_admin = is_group_admin or is_super_admin

    # 如果直接清空
    if clear:
        if is_super_admin:
            util.delete_message_image_file(ans_list)
            _db.pop(key)
            return '清空成功~'
        else:
            return '木有权限啦~~'

    if config['rule']['question_del_last']:
        ans_list.reverse()

    is_del_flag = False

    for index, value in enumerate(ans_list):
        # 如果不是本群就跳过  或者 是超级管理员的话 就继续删除
        if value['group_id'] != ctx['group_id'] and not (is_super_admin and value['user_id'] in admins):
            continue
        # 管理员则直接删除第一个元素
        if is_admin:
            if not config['rule']['can_delete_super_admin_qa'] and \
                    value['user_id'] in admins and \
                    not is_super_admin:
                # 不允许删除超级管理员的设置
                continue
            else:
                is_del_flag = True
                util.delete_message_image_file(value)
                ans_list.pop(index)
                break
        else:
            # 如果不是管理员 就删除自己的第一个元素
            if value['user_id'] == ctx['user_id']:
                is_del_flag = True
                util.delete_message_image_file(value)
                ans_list.pop(index)
                break

    # 表示删除了元素 可以更新数据库了
    if is_del_flag:
        # 如果刚刚反转了 那要反转回来
        if config['rule']['question_del_last']:
            ans_list.reverse()
        if bool(ans_list):
            _db[key] = ans_list
        else:
            _db.pop(key)

    return '删除成功啦' if is_del_flag else '删除失败 可能木有权限'


def _locate_question_key(target):
    """在两张表里找出用户所指的那个问题，返回 (数据库, 键)。
    找不到返回 (None, None)。

    用户不需要记住「正则问答要加 R」，这里把几种可能的写法都试一遍：
      1. 普通表精确匹配（最常见，先试最省的）
      2. 待删除内容以 R 开头 -> 剥掉 R 再查正则表
      3. 待删除内容以 A|B|C 形式给出 -> 转成正则再查正则表
      4. 以上都不中 -> 直接拿原文查正则表（用户的词本身就是正则）
    """
    if target in db:
        return db, target

    reg_cmd = config['str']['reg_match_cmd']
    stripped = util.get_msg_keyword(reg_cmd, target, True)
    if stripped:
        if stripped in reg_db:
            return reg_db, stripped
        # 用户加了 R 但内容其实是多关键词写法（R主人|圆妈），剥掉 R 后还要按多关键词再转一次
        if util.split_multi_keyword(stripped):
            built = util.build_multi_keyword_regex(stripped)
            if built in reg_db:
                return reg_db, built

    multi = util.split_multi_keyword(target)
    if multi:
        built = util.build_multi_keyword_regex(target)
        if built in reg_db:
            return reg_db, built

    if target in reg_db:
        return reg_db, target

    return None, None


def _slice_message_segments(message, raw, start):
    """取出 raw[start:] 对应的消息段。raw 必须等于 str(message)：
    起点之前的段整个丢弃，起点所在的文本段只保留后半截，
    图片/@等非文本段原样保留。"""
    segs = []
    offset = 0
    for seg in message:
        text = str(seg)
        end = offset + len(text)
        if end <= start:
            offset = end
            continue
        if seg['type'] == 'text' and start > offset:
            seg = MessageSegment.text(text[start - offset:])
        segs.append(seg)
    return segs


# 更换回答xxx 内容：问题只有一条回答时直接替换回答内容
async def replace_answer(ctx):
    prefix = config['comm'].get('answer_replace')
    if not prefix:
        return None
    raw = str(ctx['message'])
    rest = util.get_msg_keyword(prefix, raw.lstrip(), True)
    if not isinstance(rest, str):
        return None

    # 「更换回答」和问题之间的空白可有可无。问题与内容的分隔点有两类：
    # 空白（发文字的习惯），或直接跟图片等非文本段（发图顺手不加空格）。
    # 问题本身也可能带空格，所以逐个分隔点试：先全部按原文精确匹配，
    # 都不中再把候选问题里的空白去掉兜底一遍（容忍「小 圆」这类手滑空格）；
    # 一个都定位不到说明不是冲着已有问答来的，返回 None 走原有流程。
    bounds = [(m.start(), m.end()) for m in re.finditer(r'\s+', rest)]
    bounds += [(m.start(), m.start()) for m in re.finditer(r'\[CQ:[^\]]*\]', rest)]
    bounds.sort()

    hit = None
    for strip_ws in (False, True):
        for s, e in bounds:
            q = rest[:s].strip()
            if not q:
                continue
            cand = re.sub(r'\s+', '', q) if strip_ws else q
            _db, key = _locate_question_key(cand)
            if _db is not None:
                hit = (_db, key, cand, e)
                break
        if hit:
            break
    if not hit:
        return None
    _db, key, q, content_off = hit

    # 只统计本群可见的回答（超管设置的全群问答也算），口径和回答时一致
    full = _db.get(key, [])
    cands = util.filter_list(full, lambda x: x['group_id'] == ctx['group_id'] or (
        x['user_id'] in admins if config['rule']['super_admin_is_all_group'] else False))
    if not cands:
        return None

    content = rest[content_off:].strip()
    if not content:
        return '回答呢? 回答呢??'

    if len(cands) > 1:
        # 多条回答不动库里的数据：照常随机回一条（借 answer 的完整筛选逻辑），
        # 再提示怎么删到只剩一条
        ctx2 = dict(ctx)
        ctx2['message'] = Message(key)
        ctx2['raw_message'] = key
        reply = await answer(ctx2)
        hint = (f'这个问题有多条回答，我就不直接更换啦。'
                f'可以先发「{config["comm"]["answer_delete"]}{q}」删除多余的回答（每发一次删一条），'
                f'删到只剩一条后，再发「{prefix}{q} 新内容」就能直接更换了')
        if reply:
            return list(reply) + [MessageSegment.text('\n' + hint)]
        return hint

    # 只有一条：权限对齐删除规则——本人或管理员可改，群管改超管的设置看 can_delete_super_admin_qa
    rec = cands[0]
    is_super_admin = ctx['user_id'] in admins
    if rec['user_id'] != ctx['user_id']:
        if not (is_super_admin or util.is_group_admin(ctx)):
            return '这条回答不是你设置的，不能更换哦'
        if not is_super_admin and rec['user_id'] in admins and \
                not config['rule']['can_delete_super_admin_qa']:
            return '这条回答是超级管理员设置的，木有权限更换哦'

    # 新内容从原消息里切出来，图片要走一遍缓存，和设置问答时一样
    head_len = len(raw) - len(raw.lstrip())
    new_msg = []
    for seg in _slice_message_segments(ctx['message'], raw, head_len + len(prefix) + content_off):
        if seg['type'] == 'image':
            seg = await util.ms_handler_image(seg, config['rule']['use_cq_code_image_url'],
                                              config['cache_dir'], b64=config['image_base64'])
            if not seg:
                return '图片缓存失败了啦！'
        new_msg.append(seg)
    if not new_msg:
        return '回答呢? 回答呢??'

    # 旧回答里的缓存图一并清掉；full 是刚解码出来的副本，改完整体写回才落库
    util.delete_message_image_file(rec)
    rec['message'] = new_msg
    _db[key] = full
    return '更换成功啦 来问问我吧！'
