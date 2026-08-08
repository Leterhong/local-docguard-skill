#!/usr/bin/env python3
# Minimal Markdown -> standalone HTML converter for the DocGuard AI article.
# Uses the `markdown` package (already in the venv) with tables + fenced_code.
import sys, os, argparse
from markdown import Markdown

CSS = """
:root{--fg:#1F2328;--muted:#57606A;--line:#D0D7DE;--green:#1F883D;--blue:#0969DA;}
*{box-sizing:border-box}
body{font-family:'Segoe UI','Microsoft YaHei',system-ui,sans-serif;color:var(--fg);
  max-width:920px;margin:0 auto;padding:40px 24px 80px;line-height:1.75;font-size:16px;}
h1{font-size:30px;line-height:1.3;margin:.4em 0 .6em;border-bottom:3px solid var(--green);padding-bottom:.3em}
h2{font-size:23px;margin:1.8em 0 .6em;padding-left:.5em;border-left:5px solid var(--green)}
h3{font-size:19px;margin:1.4em 0 .4em;color:#0f5323}
p{margin:.6em 0}
blockquote{margin:.8em 0;padding:.4em 1em;background:#F6F8FA;border-left:4px solid var(--line);color:var(--muted)}
code{background:#F6F8FA;padding:.15em .4em;border-radius:4px;font-size:13.5px;font-family:Consolas,'Cascadia Code',monospace}
pre{background:#0d1117;color:#e6edf3;padding:16px 18px;border-radius:10px;overflow:auto;line-height:1.55}
pre code{background:none;color:inherit;padding:0}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:14.5px}
th,td{border:1px solid var(--line);padding:8px 12px;text-align:left}
th{background:#F6F8FA;font-weight:700}
img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:8px;margin:1em 0;
  box-shadow:0 1px 4px rgba(0,0,0,.08)}
em{color:var(--muted);font-style:italic}
hr{border:none;border-top:1px solid var(--line);margin:2em 0}
a{color:var(--blue);text-decoration:none}
ul,ol{padding-left:1.4em}
li{margin:.3em 0}
"""

def convert(md_path: str, out_path: str):
    md = Markdown(extensions=["tables", "fenced_code", "toc", "nl2br"])
    with open(md_path, encoding="utf-8") as f:
        body = md.convert(f.read())
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>DocGuard AI — 本地企业文档智能审查 Agent Skill</title>
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"written {out_path} ({len(html)} bytes)")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("out", nargs="?")
    a = ap.parse_args()
    out = a.out or (os.path.splitext(a.md)[0] + ".html")
    convert(a.md, out)
