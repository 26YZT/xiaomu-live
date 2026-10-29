"""Idempotent first-run setup. Never overwrites user profiles, media or secrets."""
from pathlib import Path
import json
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]


def json_file(path, value):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def setup():
    avatar = ROOT / "assets/avatars/xiaoshen"
    gallery = ROOT / "assets/gallery/files"
    for folder in (avatar, gallery, ROOT / "personas", ROOT / ".state", ROOT / "logs"):
        folder.mkdir(parents=True, exist_ok=True)
    for color in ("navy", "green"):
        source = ROOT / "starter" / f"xiaomu-{color}.png"
        for target in (avatar / f"{color}.png", gallery / f"starter-{color}.png"):
            if not target.exists():
                shutil.copyfile(source, target)
    persona = ROOT / "personas/xiaomu.md"
    if not persona.exists():
        shutil.copyfile(ROOT / "starter/persona.md", persona)
    profile = {"name": "小沐", "note": "原创 AI 示例角色 · 可替换形象和人设",
               "image": "assets/avatars/xiaoshen/navy.png", "voice": "Sunnybobi", "persona_file": "personas/xiaomu.md",
               "outfits": "assets/avatars/xiaoshen/outfits.json",
               "greeting_instruction": "用一句简短自然的话介绍自己是 AI 主播，问问大家今天过得怎么样。",
               "idle_topics": ["聊聊今天的一件小确幸", "说说你喜欢的雨天活动", "聊聊最近想读的书", "分享一个整理桌面的小习惯", "问问大家更喜欢咖啡还是茶", "聊聊周末想做的一件小事"]}
    json_file(ROOT / "assets/avatars/profiles.json", {"xiaoshen": profile})
    json_file(avatar / "outfits.json", {"items": [
        {"name": "深蓝毛衣", "aliases": ["蓝色毛衣", "蓝毛衣", "深蓝", "日常"], "file": "assets/avatars/xiaoshen/navy.png", "base": True, "description": "原创示例底图"},
        {"name": "森林绿毛衣", "aliases": ["绿色毛衣", "绿毛衣", "绿色", "森林绿"], "file": "assets/avatars/xiaoshen/green.png", "description": "同一角色、同一场景的预制换装图"},
    ]})
    json_file(ROOT / "assets/gallery/gallery.json", {"items": [
        {"id": "starter-" + color, "kind": "image", "file": f"gallery/files/starter-{color}.png", "source": "原创示例角色",
         "prompt": label, "model": "imagegen", "created": time.time(), "cost_cny": None}
        for color, label in (("navy", "小沐 · 深蓝毛衣"), ("green", "小沐 · 森林绿毛衣"))
    ]})
    cfg = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    cfg["avatars"] = {"xiaoshen": profile}
    cfg["vidu"]["api_key_file"] = ".secrets/vidu.key"
    cfg["generator"].update(api_key_file=".secrets/evolink.key", enabled=False)
    cfg["broadcast"] = {"youtube_key_file": ".secrets/youtube.key"}
    cfg["schedule"].update(policy="fifo", auto_recover=False, demo_interval_seconds=5)
    cfg["safety"].update(budget_credits=900, max_session_minutes=10, daily_budget_credits=3200,
                         live_visual_edits=False, automatic_outfits=False, gift_priority_enabled=False, large_actions=False)
    json_file(ROOT / "config.json", cfg)
    json_file(ROOT / "assets/stage_panel.json", {
        "title": "AI 数字人直播", "can_title": "互动玩法", "can": ["聊天接话", "预制换装", "比心挥手"],
        "cannot_title": "当前不开放", "cannot": ["现场换背景", "生成道具", "唱歌跳舞"],
        "tips_title": "发条弹幕", "good": ["你好呀", "换上绿色毛衣", "比个心"], "bad": ["广告引流", "重复刷屏"],
        "outfit_hint": {"default": "深蓝毛衣 / 森林绿毛衣"}
    })


if __name__ == "__main__":
    setup()
    print("本地资料已就绪；已有文件保持不变。")
