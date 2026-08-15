#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔁 搬运前去重 —— 微信里很多文件，你电脑上早就有了

两类重复：
  ① 本机已有：你自己收藏过、自己发出去的，微信里那份只是副本
  ② 群发副本：同一个文件被好几个群转发，下载成 小耳简历.pdf / 小耳简历(1).pdf

判定标准（保守，宁可漏判不可误判）：
  · 文件名 + 字节数完全相同        → 判定重复
  · 字节数相同 + 文件名是副本变体   → 判定重复
  · 只有字节数相同、名字完全不同    → 不判定，只列出来给人看

重复的一律不删除，单独归到 _重复_电脑里已有/ 由用户自己处置。
"""

import os
import re
import hashlib
from pathlib import Path
from collections import defaultdict

# 建索引时跳过：依赖包、版本库、缓存 —— 扫进来纯属噪音，还拖慢速度
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    "Library", "dist", "build", ".next", ".cache", "site-packages",
    "Pods", ".gradle", "target", ".terraform", "vendor",
}
# 副本命名：微信/浏览器的 xxx(2)，macOS 的 xxx 2，会叠加成 xxx(1) 2
DUP_PATTERNS = [
    re.compile(r"^(.*?)[\s_-]*\(\d+\)(\.[^.]*)?$"),
    re.compile(r"^(.*?)[\s_-]+\d+(\.[^.]*)?$"),
]
MIN_SIZE = 4096          # 小于 4KB 的不参与，同名小文件太容易撞


def base_name(name: str) -> str:
    """把 '小耳简历(1) 2.pdf' 一路剥成 '小耳简历.pdf'"""
    cur, prev = name, None
    while cur != prev:
        prev = cur
        for pat in DUP_PATTERNS:
            m = pat.match(cur)
            if m:
                stripped = f"{m.group(1)}{m.group(2) or ''}"
                if stripped and stripped != cur:
                    cur = stripped
                    break
    return cur


def build_index(roots, on_progress=None):
    """扫本机，建 {(归一化名, 字节数): [路径]} 索引。只读，不碰任何文件。"""
    idx = defaultdict(list)
    n = 0
    for root in roots:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                p = Path(dirpath) / fn
                try:
                    sz = p.stat().st_size
                except OSError:
                    continue
                if sz < MIN_SIZE:
                    continue
                idx[(base_name(fn), sz)].append(p)
                n += 1
                if on_progress and n % 20000 == 0:
                    on_progress(n)
    return idx, n


def digest(path: Path, chunk=1 << 20) -> str:
    """头 1M + 尾 1M + 字节数。大文件不必全读，够区分了。"""
    try:
        sz = path.stat().st_size
        h = hashlib.md5(str(sz).encode())
        with path.open("rb") as f:
            h.update(f.read(chunk))
            if sz > chunk * 2:
                f.seek(-chunk, os.SEEK_END)
                h.update(f.read(chunk))
        return h.hexdigest()
    except OSError:
        return ""


def check(files, idx):
    """比对。返回 (确定重复, 疑似, 组内副本)"""
    dup, suspect = [], []

    # ① 跟本机索引比
    seen = set()
    for f in files:
        try:
            sz = f.stat().st_size
        except OSError:
            continue
        if sz < MIN_SIZE:
            continue
        key = (base_name(f.name), sz)
        hits = [p for p in idx.get(key, []) if p != f]
        if hits:
            dup.append((f, hits[0], "本机已有同名同大小"))
            seen.add(f)

    # ② 微信内部的群发副本：归一化后同名同大小的，只留一个
    groups = defaultdict(list)
    for f in files:
        if f in seen:
            continue
        try:
            groups[(base_name(f.name), f.stat().st_size)].append(f)
        except OSError:
            pass
    for members in groups.values():
        if len(members) < 2:
            continue
        # 留名字最短的那个（通常是没有 (N) 后缀的原始版）
        members.sort(key=lambda p: (len(p.name), str(p)))
        for extra in members[1:]:
            dup.append((extra, members[0], "群发重复下载"))
            seen.add(extra)

    # ③ 大小相同但名字不同 —— 只报告，不判定
    by_size = defaultdict(list)
    for key, paths in idx.items():
        by_size[key[1]].extend(paths)
    for f in files:
        if f in seen:
            continue
        try:
            sz = f.stat().st_size
        except OSError:
            continue
        cand = [p for p in by_size.get(sz, []) if p != f][:1]
        if cand and digest(f) and digest(f) == digest(cand[0]):
            suspect.append((f, cand[0], "字节数与内容一致，但文件名不同"))

    return dup, suspect


if __name__ == "__main__":
    import sys
    roots = sys.argv[1:] or ["~/Desktop", "~/Documents", "~/Downloads"]
    print(f"📇 建索引：{', '.join(roots)}")
    idx, n = build_index(roots, lambda c: print(f"   …{c} 个", flush=True))
    print(f"   共 {n} 个文件，{len(idx)} 个去重键\n")
