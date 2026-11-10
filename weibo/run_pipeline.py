"""通用微博事件流水线：搜索 -> 选帖 -> 评论/回复 -> 媒体。"""
import argparse
import hashlib
import json
import re
from pathlib import Path

from crawl_keywords import main as search_main
from crawl_comments_batch import main as comments_main
from fetch_media_batch import main as media_main


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    required = ["event_slug", "event_name", "search_queries"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"事件配置缺少字段：{', '.join(missing)}")
    base = f"weibo/results/{config['event_slug']}"
    config.setdefault("cookie_file", "cookies.txt")
    config.setdefault("search_output_dir", f"{base}/search")
    config.setdefault("comments_output_dir", f"{base}/comments")
    config.setdefault("media_output_dir", f"{base}/media")
    config.setdefault("include_replies", True)
    return config


def topic_keywords(config):
    """优先使用可选关键词；未填写时直接从搜索词中自动拆出。"""
    if config.get("keywords"):
        return list(dict.fromkeys(config["keywords"]))
    words = []
    for query in config["search_queries"]:
        words.extend(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", query))
    return list(dict.fromkeys(word for word in words if len(word) >= 2))


def matches_post(text, config):
    lowered = (text or "").lower()
    required_terms = config.get("required_terms") or []
    anchors = config.get("anchors") or []
    groups = config.get("post_required_groups") or []
    phrases = config.get("post_event_phrases") or []
    excluded = config.get("post_exclude_phrases") or []
    if any(word.lower() in lowered for word in excluded):
        return False
    if required_terms and not all(word.lower() in lowered for word in required_terms):
        return False
    if anchors and not any(anchor.lower() in lowered for anchor in anchors):
        return False
    if groups and not all(
        any(word.lower() in lowered for word in group) for group in groups
    ):
        return False
    if phrases and not any(phrase.lower() in lowered for phrase in phrases):
        return False
    # 未配置高级规则时，退回简单关键词命中数量。
    if not groups and not phrases:
        hits = sum(word.lower() in lowered for word in topic_keywords(config))
        return hits >= int(config.get("min_post_keyword_hits", 1))
    return True


def safe_label(index, content):
    text = re.sub(r'[\\/:*?"<>|\r\n]+', "_", content or "帖子")
    return f"{index:02d}_{text[:24]}"


def select_posts(results, config):
    selected, seen = [], set()
    keywords = topic_keywords(config)
    for item in results:
        if not matches_post(item.get("content", ""), config):
            continue
        url = (item.get("publish_url") or "").split("?", 1)[0]
        if not url or url in seen:
            continue
        seen.add(url)
        lowered = (item.get("content") or "").lower()
        keyword_hits = sum(word.lower() in lowered for word in keywords)
        selected.append((item, url, keyword_hits))
    selected.sort(
        key=lambda pair: (
            pair[2], pair[0].get("comment_count", 0),
            pair[0].get("like_count", 0)
        ),
        reverse=True,
    )
    max_posts = int(config.get("max_posts", 5))
    return [
        (safe_label(index, item.get("content", "")), url,
         item.get("content", ""))
        for index, (item, url, _) in enumerate(selected[:max_posts], 1)
    ]


def main(config):
    config["resolved_keywords"] = topic_keywords(config)
    print(f"事件：{config['event_name']}")
    print(f"抓取模式：{config.get('mode', 'limited')}")
    print("[1/3] 搜索相关微博")
    results = search_main(
        keywords=config["search_queries"],
        cookie_path=config.get("cookie_file", "cookies.txt"),
        output_dir=config.get("search_output_dir", "weibo/results/search"),
        max_pages=int(config.get("search_max_pages", 1)),
    )
    if not results:
        raise RuntimeError(
            "微博搜索没有返回任何帖子。请确认登录状态，并改用更短、更常见的搜索词。"
        )
    posts = select_posts(results, config)
    if not posts:
        raise RuntimeError(
            f"微博返回了 {len(results)} 条帖子，但没有帖子达到关键词命中要求；"
            "请降低 --min-hits 或调整 --keywords。"
        )
    selected_path = Path(config["search_output_dir"]) / "selected_posts.json"
    selected_path.write_text(
        json.dumps([
            {"label": label, "post_url": url, "post_text": text}
            for label, url, text in posts
        ], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"自动筛出 {len(posts)} 条目标帖")
    print("[2/3] 抓取一级评论和二级回复，并进行相关性分层")
    comments_main(posts, config)
    print("[3/3] 获取图片/视频网址并下载图片")
    media_main(posts, config)
    print("自动流程完成")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="通用微博事件抓取")
    parser.add_argument("--config", help="可选：事件 JSON 配置文件")
    parser.add_argument(
        "--mode", choices=["limited", "full"], default=None,
        help="limited=按上限抓取；full=选中帖子内评论、回复和图片抓到结束",
    )
    parser.add_argument("--event", help="事件名称，例如：世界杯决赛西班牙胜利")
    parser.add_argument("--query", action="append", help="搜索词；可重复使用")
    parser.add_argument("--keywords", nargs="*", help="可选相关词，默认从搜索词提取")
    parser.add_argument(
        "--required", nargs="*",
        help="可选：帖子正文必须同时包含的核心词",
    )
    parser.add_argument(
        "--anchor", action="append",
        help="可选：核心事件短语；可重复填写，命中任意一个即可",
    )
    parser.add_argument("--posts", type=int, default=5, help="最多选择帖子数")
    parser.add_argument("--pages", type=int, default=1, help="每个搜索词抓取页数")
    parser.add_argument("--comments", type=int, default=100, help="每帖一级评论上限")
    parser.add_argument("--replies", type=int, default=50, help="每条评论的二级回复上限")
    parser.add_argument("--images", type=int, default=3, help="每帖下载图片上限")
    parser.add_argument("--min-hits", type=int, default=1, help="帖子最少命中关键词数")
    parser.add_argument("--no-replies", action="store_true", help="不抓取二级回复")
    args = parser.parse_args()
    if args.config:
        task_config = load_config(args.config)
        if args.mode:
            task_config["mode"] = args.mode
    else:
        if not args.event or not args.query:
            parser.error("不使用 --config 时，必须提供 --event 和至少一个 --query")
        digest = hashlib.sha1(args.event.encode("utf-8")).hexdigest()[:8]
        task_config = {
            "event_slug": f"event_{digest}",
            "event_name": args.event,
            "search_queries": args.query,
            "keywords": args.keywords or [],
            "required_terms": args.required or [],
            "anchors": args.anchor or [],
            "min_post_keyword_hits": args.min_hits,
            "max_posts": args.posts,
            "search_max_pages": args.pages,
            "max_comments_per_post": args.comments,
            "max_replies_per_comment": args.replies,
            "max_images_per_post": args.images,
            "include_replies": not args.no_replies,
            "mode": args.mode or "limited",
        }
        base = f"weibo/results/{task_config['event_slug']}"
        task_config.update({
            "cookie_file": "cookies.txt",
            "search_output_dir": f"{base}/search",
            "comments_output_dir": f"{base}/comments",
            "media_output_dir": f"{base}/media",
        })
    main(task_config)
