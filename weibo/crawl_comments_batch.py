import csv
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

POSTS = []

# 默认抓一级评论和二级回复；可由事件配置覆盖。
INCLUDE_REPLIES = True

# 当前事件的主题词。换事件时修改这里即可；严格结果至少要命中一个主题词。
EVENT_KEYWORDS = set()

# 明显与本事件无关、但可能因球员饭圈争论出现在评论区的内容。
OFF_TOPIC_PHRASES = set()
AD_PHRASES = {
    "加微信", "加vx", "加v信", "私信领取", "兼职日结", "刷单",
    "返利", "免费领取", "进群", "代理招募", "商务合作",
}

MAX_COMMENTS_PER_POST = 100
REQUEST_INTERVAL_SECONDS = 2
COOKIE_FILE = Path("cookies.txt")
OUTPUT_DIR = Path("weibo/results_doncic_comments")


def decode_base62(value):
    charset = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    result = 0
    for char in value:
        result = result * 62 + charset.index(char)
    return result


def url_to_mid(value):
    result = ""
    for end in range(len(value), 0, -4):
        start = max(end - 4, 0)
        number = str(decode_base62(value[start:end]))
        if start != 0:
            number = number.zfill(7)
        result = number + result
    return int(result)


def get_keyword(url):
    clean_url = url.split("?", 1)[0].rstrip("/")
    parts = clean_url.split("/")
    return parts[-2], url_to_mid(parts[-1])


def get_comment_data(data):
    created = datetime.strptime(
        data["created_at"], "%a %b %d %H:%M:%S %z %Y"
    ).strftime("%Y-%m-%d %H:%M:%S")
    user = data.get("user") or {}
    gender = {"m": "男", "f": "女"}.get(user.get("gender"), "未知")
    return (
        data.get("idstr", ""), data.get("rootidstr", ""), created,
        user.get("id", ""), user.get("screen_name", ""),
        data.get("text_raw", ""), data.get("like_counts", 0),
        data.get("total_number", 0), gender,
    )


def load_cookie_header(cookie_file=COOKIE_FILE):
    cookies = json.loads(Path(cookie_file).read_text(encoding="utf-8"))
    return "; ".join(
        f"{item['name']}={item['value']}"
        for item in cookies
        if item.get("name") and item.get("value")
    )


def fetch_json(url, cookie_header):
    request = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie_header,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
            ),
            "Referer": "https://weibo.com/",
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def comment_page(uid, mid, max_id, fetch_level, cookie_header):
    params = {
        "flow": 1,
        "is_reload": 1,
        "id": mid,
        "is_show_bulletin": 2,
        "is_mix": 1 if fetch_level == 1 else 0,
        "count": 20,
        "uid": uid,
        "fetch_level": fetch_level,
        "locale": "zh-CN",
    }
    if max_id:
        params["max_id"] = max_id
    url = "https://weibo.com/ajax/statuses/buildComments?" + urllib.parse.urlencode(params)
    return fetch_json(url, cookie_header)


def normalize_comment(raw, post_label, post_url, level, post_text=""):
    (comment_id, root_id, created_at, user_id, screen_name,
     text, like_count, reply_count, gender) = get_comment_data(raw)
    return {
        "post_label": post_label,
        "post_url": post_url,
        "comment_id": comment_id,
        "parent_comment_id": "" if level == 0 else root_id,
        "level": level,
        "user_id": user_id,
        "screen_name": screen_name,
        "gender": gender,
        "created_at": created_at,
        "text": text,
        "like_count": like_count,
        "reply_count": reply_count,
        "post_text": post_text,
        "parent_text": "",
        "context_text": "",
    }


def clean_text(value):
    value = re.sub(r"https?://\S+|http://t\.cn/\S+", " ", value or "")
    value = re.sub(r"回复@[^:：\s]+[:：]?", " ", value)
    value = re.sub(r"\[[^\]]+\]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def garbage_reason(text):
    """只过滤跨事件都成立的垃圾特征，不依赖人工事件规则。"""
    lowered = text.lower()
    meaningful = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text)
    if not meaningful:
        return "纯表情、标点或链接"
    if len(meaningful) < 2:
        return "信息量过低"
    compact = "".join(meaningful).lower()
    if len(compact) >= 4 and len(set(compact)) == 1:
        return "重复字符"
    if any(phrase in lowered for phrase in AD_PHRASES):
        return "疑似广告或引流"
    return ""


def classify_relevance(row, event_keywords=None, off_topic_phrases=None,
                       parent_status="", parent_text=""):
    """保守地判断评论能否用于事件立场标注，不把模糊内容强行判成相关。"""
    text = clean_text(row.get("text", ""))
    lowered = text.lower()
    event_keywords = set(EVENT_KEYWORDS if event_keywords is None else event_keywords)
    off_topic_phrases = set(
        OFF_TOPIC_PHRASES if off_topic_phrases is None else off_topic_phrases
    )
    hits = sorted(word for word in event_keywords if word.lower() in lowered)
    off_topic = sorted(word for word in off_topic_phrases if word in text)
    garbage = garbage_reason(text)
    row["parent_text"] = clean_text(parent_text)
    row["context_text"] = " | ".join(filter(None, [
        clean_text(row.get("post_text", "")), row["parent_text"], text
    ]))
    if garbage:
        status, reason = "garbage", garbage
    elif off_topic:
        status, reason = "irrelevant", "命中明确的题外内容"
    elif hits:
        status, reason = "relevant", "命中事件主题词"
    elif row.get("level") == 1 and parent_status in {"relevant", "contextual"}:
        status, reason = "contextual", "结合一级评论后与事件相关"
    elif row.get("level") == 0:
        status, reason = "contextual", "直接回复已通过关键词筛选的原帖"
    else:
        status, reason = "uncertain", "结合父评论后仍无法判断"
    row["clean_text"] = text
    row["relevance_status"] = status
    row["relevance_reason"] = reason
    row["matched_keywords"] = "|".join(hits)
    return row


def fetch_all_replies(raw_parent, label, post_url, post_text, cookie_header,
                      event_keywords, off_topic_phrases, parent_status,
                      max_replies):
    """抓取一条一级评论下面的完整二级回复分页。"""
    root_id = raw_parent.get("idstr") or str(raw_parent.get("id") or "")
    root_user = raw_parent.get("user") or {}
    root_uid = str(root_user.get("id") or "")
    parent_text = raw_parent.get("text_raw", "")
    if not root_id or not root_uid:
        return []
    rows, max_id, seen, seen_cursors = [], "", set(), set()
    unlimited = max_replies <= 0
    while unlimited or len(rows) < max_replies:
        response = comment_page(root_uid, root_id, max_id, 1, cookie_header)
        if response.get("ok") == 0 or "data" not in response:
            break
        replies = response.get("data") or []
        if not replies:
            break
        for child in replies:
            child_id = child.get("idstr") or str(child.get("id") or "")
            if not child_id or child_id in seen:
                continue
            seen.add(child_id)
            row = normalize_comment(child, label, post_url, 1, post_text)
            # 微博返回的 rootid 是一级评论；显式保留该关联。
            row["parent_comment_id"] = root_id
            rows.append(classify_relevance(
                row, event_keywords, off_topic_phrases, parent_status,
                parent_text,
            ))
            if not unlimited and len(rows) >= max_replies:
                break
        next_max_id = response.get("max_id", 0)
        if not next_max_id or next_max_id in seen_cursors:
            break
        seen_cursors.add(next_max_id)
        max_id = next_max_id
        time.sleep(REQUEST_INTERVAL_SECONDS)
    return rows


def crawl_post(label, post_url, post_text, cookie_header, config=None):
    config = config or {}
    event_keywords = config.get("comment_keywords") or config.get(
        "resolved_keywords", EVENT_KEYWORDS
    )
    off_topic_phrases = config.get("exclude_phrases")
    if off_topic_phrases is None:
        off_topic_phrases = OFF_TOPIC_PHRASES if not config else []
    include_replies = config.get("include_replies", INCLUDE_REPLIES)
    max_comments = int(config.get("max_comments_per_post", MAX_COMMENTS_PER_POST))
    max_replies = int(config.get("max_replies_per_comment", 50))
    if config.get("mode", "limited") == "full":
        max_comments = 0
        max_replies = 0
    uid, mid = get_keyword(post_url)
    rows = []
    max_id = ""
    seen_cursors = set()
    top_level_count = 0
    unlimited_comments = max_comments <= 0
    while unlimited_comments or top_level_count < max_comments:
        response = comment_page(uid, mid, max_id, 0, cookie_header)
        if response.get("ok") == 0 or "data" not in response:
            raise RuntimeError(f"微博评论接口返回异常: {response}")
        comments = response.get("data") or []
        if not comments:
            break
        for raw in comments:
            parent = classify_relevance(
                normalize_comment(raw, label, post_url, 0, post_text),
                event_keywords, off_topic_phrases,
            )
            rows.append(parent)
            top_level_count += 1
            if not unlimited_comments and top_level_count >= max_comments:
                break
            if include_replies and raw.get("total_number", 0):
                rows.extend(fetch_all_replies(
                    raw, label, post_url, post_text, cookie_header,
                    event_keywords, off_topic_phrases,
                    parent["relevance_status"], max_replies,
                ))
        next_max_id = response.get("max_id", 0)
        if not next_max_id or next_max_id in seen_cursors:
            break
        seen_cursors.add(next_max_id)
        max_id = next_max_id
        time.sleep(REQUEST_INTERVAL_SECONDS)
    return rows


def main(posts=None, config=None):
    config = config or {}
    cookie_file = Path(config.get("cookie_file", COOKIE_FILE))
    output_dir = Path(config.get("comments_output_dir", OUTPUT_DIR))
    if not cookie_file.exists():
        raise FileNotFoundError("未找到 cookies.txt，请先重新运行关键词脚本并登录微博。")
    output_dir.mkdir(parents=True, exist_ok=True)
    cookie_header = load_cookie_header(cookie_file)
    all_rows = []
    target_posts = posts or POSTS
    if not target_posts:
        raise ValueError("未提供目标帖子，请通过 run_pipeline.py 启动任务。")
    for post in target_posts:
        label, post_url = post[0], post[1]
        post_text = post[2] if len(post) > 2 else ""
        print(f"开始抓取：{label}")
        rows = crawl_post(label, post_url, post_text, cookie_header, config)
        all_rows.extend(rows)
        print(f"完成：{label}，获得 {len(rows)} 条评论/回复")
        time.sleep(REQUEST_INTERVAL_SECONDS)

    # 同一帖子下完全相同的评论仅保留第一条，其余标为垃圾但仍留在原始表。
    seen_texts = set()
    for row in all_rows:
        fingerprint = re.sub(r"\W+", "", row.get("clean_text", "")).lower()
        key = (row.get("post_url"), fingerprint)
        if fingerprint and key in seen_texts:
            row["relevance_status"] = "garbage"
            row["relevance_reason"] = "重复评论"
        elif fingerprint:
            seen_texts.add(key)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = config.get("event_slug", "event")
    output = output_dir / f"{slug}_comments_raw_{timestamp}.csv"
    fields = list(all_rows[0].keys()) if all_rows else [
        "post_label", "post_url", "comment_id", "parent_comment_id", "level",
        "user_id", "screen_name", "gender", "created_at", "text",
        "like_count", "reply_count",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)
    strict_rows = [row for row in all_rows
                   if row["relevance_status"] in {"relevant", "contextual"}]
    strict_output = output_dir / f"{slug}_comments_relevant_{timestamp}.csv"
    with strict_output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(strict_rows)
    review_rows = [row for row in all_rows if row["relevance_status"] == "uncertain"]
    review_output = output_dir / f"{slug}_comments_review_{timestamp}.csv"
    with review_output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(review_rows)
    print(f"全部完成：原始 {len(all_rows)} 条，严格相关 {len(strict_rows)} 条")
    print(f"原始数据：{output}")
    print(f"严格相关：{strict_output}")
    print(f"待人工复核：{review_output}")


if __name__ == "__main__":
    print("请使用 weibo/run_pipeline.py 启动采集；运行参数见 README.md。")
