import json
import os
import httpx
from openai import OpenAI

class LLMTranslator:
    def __init__(self, api_key=None, model="gemini-1.5-pro", proxy_url=None, base_url=None):
        """
        初始化翻译器 (V2版：统一使用 OpenAI 兼容协议并显式代理)
        """
        self.api_key = api_key
        self.model = model
        self.proxy_url = proxy_url
        self.base_url = base_url
        
        if not self.api_key:
            print("警告：未提供 API Key！")
            self.client = None
            return

        # V2 核心：配置 httpx Client，彻底解决国内 443 / 连通性问题
        http_client = None
        if self.proxy_url:
            print(f"🔗 正在通过代理连接 LLM: {self.proxy_url}")
            http_client = httpx.Client(proxy=self.proxy_url)

        # Gemini 可以通过修改 base_url，完美使用 OpenAI 的 SDK 调用
        if "gemini" in self.model.lower():
            # 使用 Google AI Studio 兼容的 OpenAI 路由
            self.base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
            
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            http_client=http_client
        )
        
    def _build_prompt(self, text_list):
        prompt = """
你是一个专业的日本漫画汉化组翻译人员。
我将提供这一页漫画中提取出来的所有日文台词。
你需要将它们翻译成自然、流畅、符合中文读者阅读习惯的台词。

规则：
1. 请根据所有台词的上下文推断说话者的语气、身份。
2. 对于拟声词、背景音效，可以翻译为简短的中文词（如"咚"、"啪嗒"）。
3. 如果原文看起来像是 OCR 识别错误的乱码（没有意义），你可以尝试纠正，或者直接返回空字符串。
4. **必须且只能**返回一个包含翻译结果的纯 JSON 数组，**绝对不要**包含 Markdown 代码块标记 (如 ```json)。数组的长度和顺序必须与输入完全一致。

输入示例：
[
    {"id": 0, "text": "おはようございます！"},
    {"id": 1, "text": "ドカーン"}
]

输出示例：
[
    {"id": 0, "translation": "早上好！"},
    {"id": 1, "translation": "轰隆"}
]
"""
        user_input = [{"id": i, "text": text} for i, text in enumerate(text_list)]
        return prompt, json.dumps(user_input, ensure_ascii=False)

    def translate_batch(self, bboxes):
        if not self.client:
            print("未配置 API 客户端，跳过翻译。")
            return self._mock_translation(bboxes)

        texts_to_translate = []
        mapping = {}
        
        current_id = 0
        for i, bbox in enumerate(bboxes):
            jp_text = bbox.get("original_text", "").strip()
            if jp_text:
                texts_to_translate.append(jp_text)
                mapping[current_id] = i
                current_id += 1
            else:
                bbox["translated_text"] = ""

        if not texts_to_translate:
            return bboxes

        system_prompt, user_content = self._build_prompt(texts_to_translate)
        
        try:
            print(f"正在请求 {self.model} 翻译 {len(texts_to_translate)} 句台词...")
            
            # 使用统一的 Chat Completions 接口
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.3
            )
            
            result_text = response.choices[0].message.content
            print(f"LLM 原始返回: {result_text}")
            
            # 清理可能的 markdown 标记
            result_text = result_text.strip()
            if result_text.startswith("```json"):
                result_text = result_text[7:-3].strip()
            elif result_text.startswith("```"):
                result_text = result_text[3:-3].strip()
                
            parsed_data = json.loads(result_text)
            if isinstance(parsed_data, dict):
                for key in parsed_data:
                    if isinstance(parsed_data[key], list):
                        parsed_data = parsed_data[key]
                        break

            for item in parsed_data:
                item_id = item.get("id")
                translation = item.get("translation", "")
                
                if item_id in mapping:
                    original_idx = mapping[item_id]
                    bboxes[original_idx]["translated_text"] = translation
                    
            return bboxes
            
        except Exception as e:
            # V2版：输出更详尽的网络/代理错误日志
            print(f"❌ LLM 翻译发生严重错误: {e}")
            if "Connection" in str(e) or "Timeout" in str(e):
                print("⚠️ 这通常是因为网络不通或被屏蔽，请尝试在界面左侧配置 HTTP Proxy 代理地址。")
            
            for i, bbox in enumerate(bboxes):
                if not bbox.get("translated_text"):
                    bbox["translated_text"] = "[翻译失败]"
            return bboxes

    def _mock_translation(self, bboxes):
        for i, bbox in enumerate(bboxes):
            bbox["translated_text"] = f"[未翻译: {bbox.get('original_text', '')}]"
        return bboxes
