import tempfile
from entities.history import History
import imgkit
from entities import config
from entities.editor import image_clip

REDDIT_COVER_HTML = """
<!DOCTYPE html>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&family=Sigmar&display=swap" rel="stylesheet">
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <style>
    .post-cover {{
      width: 1950px;
      height: 650px;
      padding: 50px;
      flex-direction: column;
      background-color: #FFFFFF;
      border-radius: 50px;
      box-shadow: 0px 4px 4px rgba(0, 0, 0, 0.25);
      gap: 10px;
    }}

    .title-container {{
      display: -webkit-box;
      align-items: top;
      gap: 10px;
      flex-direction: row;
    }}

    .text-container {{
      display: -webkit-box;
      flex-direction: row;
      gap: 50px;
      align-items: center;
      height: fit-content;
    }}

    .title-container img {{
      width: 200px;
      height: 200px;
      border-radius: 50%;
      object-fit: cover;
      margin-right: 10px
    }}

    .text-container h1 {{
      font-size: 64px;
      font-weight: bold;
      color: #000000;
      font-family: "Inter", serif;
      height: fit-content;
      margin-right: 50px
    }}

    .text-container h2 {{
      font-size: 48px;
      font-weight: normal;
      color: #5C5C5C;
      font-family: "Inter", serif;
      height: fit-content;
    }}

    .history-title {{
        display: -webkit-box;
        align-items: center;
        -webkit-box-pack: center;
        height: 100%;
    }}
    .history-title h1 {{
        text-align: center;
        font-size: {title_font_size}px;
        font-family: "Inter", serif;
        font-weight: medium;
    }}
  </style>
</head>
<div class="post-cover">
    <div class="title-container">
        <img src="{community_url_photo}" alt="Community photo">
        <div class="text-container">
          <h1>{community}</h1>
          <h2>{post_author}</h2>
        </div>
    </div>
    <div class="history-title">
        <h1>{title}</h1>
    </div>
</div>
</html>
"""


def __generate_reddit_cover(
    title: str,
    community: str,
    author: str,
    community_url_photo: str,
    output_path: str,
    config: config.CoverConfig = config.CoverConfig(),
):
    html_content = REDDIT_COVER_HTML.format(
        title=title,
        community=community,
        post_author=author,
        community_url_photo=community_url_photo,
        title_font_size=config.title_font_size,
    )

    tmp_file = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp_file.write(bytes(html_content, "UTF-8"))
    file_name = tmp_file.name
    tmp_file.close()

    options = {"--format": "png", "--transparent": ""}
    imgkit.from_file(file_name, output_path, options=options)


def generate_cover(
    history: History, cfg: config.CoverConfig = config.CoverConfig()
) -> image_clip.ImageClip:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        __generate_reddit_cover(
            title=history.title,
            community=history.reddit_community,
            author=history.reddit_post_author,
            community_url_photo=history.reddit_community_url_photo,
            config=cfg,
            output_path=temp_file.name,
        )
        return image_clip.ImageClip(temp_file.name)
