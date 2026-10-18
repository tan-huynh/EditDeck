from openai import OpenAI
import base64
import os

client = OpenAI(
    base_url="https://yunwu.ai/v1",
    api_key=os.getenv("OPENAI_API_KEY", ""),
)

prompt = """
涓€骞呭効绔ヨ鐗╂彃鐢伙紝鐢讳腑涓€鍚嶅吔鍖绘鐢ㄥ惉璇婂櫒鍚竴鍙按鐛辜宕界殑蹇冭烦銆?
"""

result = client.images.generate(
    model="gemini-3.1-flash-image-preview",
    prompt=prompt
)

image_base64 = result.data[0].b64_json
image_bytes = base64.b64decode(image_base64)

with open("otter.png", "wb") as f:
    f.write(image_bytes)
