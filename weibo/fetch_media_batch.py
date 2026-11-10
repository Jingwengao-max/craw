import json
import mimetypes
import time
import urllib.parse
import urllib.request
from pathlib import Path


POSTS = []

COOKIE_FILE = Path("cookies.txt")
OUTPUT_DIR = Path("weibo/results_doncic_media")
MAX_IMAGES_PER_POST = 3


def load_cookie_header(cookie_file=COOKIE_FILE):
    cookies = json.loads(Path(cookie_file).read_text(encoding="utf-8"))
    return "; ".join(
        f"{item['name']}={item['value']}"
        for item in cookies
        if item.get("name") and item.get("value")
    )


def make_request(url, cookie_header=""):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        ),
        "Referer": "https://weibo.com/",
        "Accept": "application/json, text/plain, */*",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    return urllib.request.Request(url, headers=headers)


def fetch_detail(post_url, cookie_header):
    post_code = post_url.rstrip("/").split("/")[-1].split("?")[0]
    query = urllib.parse.urlencode(
        {"id": post_code, "locale": "zh-CN", "isGetLongText": "true"}
    )
    url = "https://weibo.com/ajax/statuses/show?" + query
    with urllib.request.urlopen(make_request(url, cookie_header), timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_media(detail):
    image_urls = []
    video_urls = []
    mixed = (detail.get("mix_media_info") or {}).get("items") or []
    if mixed:
        for item in mixed:
            media_type = item.get("type")
            data = item.get("data") or {}
            if media_type == "pic":
                url = ((data.get("original") or {}).get("url"))
                if url:
                    image_urls.append(url)
            elif media_type == "video":
                media_info = data.get("media_info") or {}
                url = media_info.get("h5_url") or media_info.get("stream_url")
                if url:
                    video_urls.append(url)
    else:
        for value in (detail.get("pic_infos") or {}).values():
            url = ((value or {}).get("original") or {}).get("url")
            if url:
                image_urls.append(url)
        page_info = detail.get("page_info") or {}
        if page_info.get("object_type") == "video":
            media_info = page_info.get("media_info") or {}
            url = (
                media_info.get("h5_url")
                or media_info.get("stream_url")
                or page_info.get("short_url")
            )
            if url:
                video_urls.append(url)
    return list(dict.fromkeys(image_urls)), list(dict.fromkeys(video_urls))


def download_image(url, destination, cookie_header):
    with urllib.request.urlopen(make_request(url, cookie_header), timeout=30) as response:
        content_type = response.headers.get_content_type()
        if not content_type.startswith("image/"):
            raise ValueError(f"返回内容不是图片：{content_type}")
        extension = mimetypes.guess_extension(content_type) or ".jpg"
        final_path = destination.with_suffix(extension)
        final_path.write_bytes(response.read())
        return final_path


def main(posts=None, config=None):
    config = config or {}
    cookie_file = Path(config.get("cookie_file", COOKIE_FILE))
    output_dir = Path(config.get("media_output_dir", OUTPUT_DIR))
    max_images = int(config.get("max_images_per_post", MAX_IMAGES_PER_POST))
    if config.get("mode", "limited") == "full":
        max_images = 0
    if not cookie_file.exists():
        raise FileNotFoundError("未找到 cookies.txt，请先运行微博搜索并完成登录。")
    output_dir.mkdir(parents=True, exist_ok=True)
    cookie_header = load_cookie_header(cookie_file)
    records = []
    target_posts = posts or POSTS
    if not target_posts:
        raise ValueError("未提供目标帖子，请通过 run_pipeline.py 启动任务。")
    for post in target_posts:
        label, post_url = post[0], post[1]
        print(f"读取微博详情：{label}")
        detail = fetch_detail(post_url, cookie_header)
        image_urls, video_urls = extract_media(detail)
        post_id = str(detail.get("idstr") or post_url.rstrip("/").split("/")[-1])
        post_dir = output_dir / post_id
        post_dir.mkdir(parents=True, exist_ok=True)
        local_paths = []
        errors = []
        selected_images = image_urls if max_images <= 0 else image_urls[:max_images]
        for index, image_url in enumerate(selected_images, 1):
            try:
                path = download_image(
                    image_url, post_dir / f"image_{index:02d}", cookie_header
                )
                local_paths.append(str(path))
                print(f"  已下载：{path}")
            except Exception as exc:
                errors.append({"url": image_url, "error": repr(exc)})
                print(f"  下载失败：{exc}")
        records.append(
            {
                "label": label,
                "post_id": post_id,
                "post_url": post_url,
                "pic_num_reported": detail.get("pic_num", 0),
                "image_urls": image_urls,
                "video_urls": video_urls,
                "downloaded_image_paths": local_paths,
                "download_errors": errors,
            }
        )
        time.sleep(2)
    metadata_path = output_dir / "media_metadata.json"
    metadata_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"完成，媒体元数据保存至：{metadata_path}")


if __name__ == "__main__":
    print("请使用 weibo/run_pipeline.py 启动采集；运行参数见 README.md。")
