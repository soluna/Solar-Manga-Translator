import json
import os
import time

class LLMTranslator:
    def __init__(self, api_key=None, model="gpt-4o"):
        """
        初始化大语言模型翻译器，支持 OpenAI 格式和 Google Gemini
        """
        self.model = model
        self.api_key = api_key
        self.is_gemini = "gemini" in model.lower()
        
        if self.is_gemini:
            # 尝试导入 google.generativeai
            try:
                import google.generativeai as genai
                key_to_use = self.api_key or os.getenv("GEMINI_API_KEY")
                if key_to_use:
                    genai.configure(api_key=key_to_use)
                    # Use the new gemini models (e.g., gemini-1.5-pro, gemini-pro)
                    self.gemini_model = genai.GenerativeModel(self.model)
                else:
                    self.gemini_model = None
            except ImportError:
                print("请先安装 google-generativeai: pip install google-generativeai")
                self.gemini_model = None
        else:
            from openai import OpenAI
            key_to_use = self.api_key or os.getenv("OPENAI_API_KEY")
            self.client = OpenAI(api_key=key_to_use) if key_to_use else None
        
    def _build_prompt(self, text_list):
        prompt = """
你是一个专业的日本漫画汉化组翻译人员。
我将提供这一页漫画中提取出来的所有日文台词（按照从右到左、从上到下的阅读顺序排列）。
你需要将它们翻译成自然、流畅、符合中文读者阅读习惯的中文台词。

规则：
1. 请根据所有台词的上下文来推断说话者的语气、身份。
2. 对于拟声词、背景音效，可以翻译为简短的中文词（如"咚"、"啪嗒"）。
3. 如果原文看起来像是 OCR 识别错误的乱码（没有意义），你可以尝试纠正，或者直接返回空字符串。
4. **必须且只能**返回一个包含翻译结果的纯 JSON 数组（不要包含 Markdown 代码块标记如 ```json ）。数组的长度和顺序必须与输入完全一致。

输入格式示例：
[
    {"id": 0, "text": "おはようございます！"},
    {"id": 1, "text": "ドカーン"}
]

输出格式示例：
[
    {"id": 0, "translation": "早上好！"},
    {"id": 1, "translation": "轰隆"}
]
"""
        user_input = []
        for i, text in enumerate(text_list):
            user_input.append({"id": i, "text": text})
            
        return prompt, json.dumps(user_input, ensure_ascii=False)

    def translate_batch(self, bboxes):
        if self.is_gemini and not getattr(self, 'gemini_model', None):
            print("未提供 Gemini API Key 或缺少 google-generativeai 库。")
            return self._mock_translation(bboxes)
        elif not self.is_gemini and not getattr(self, 'client', None):
            print("未提供 OpenAI API Key。")
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
            print(f"正在使用 {self.model} 翻译 {len(texts_to_translate)} 句台词...")
            result_text = ""
            
            if self.is_gemini:
                # Gemini 调用方式
                # Combine system prompt and user content since Gemini API handles it slightly differently
                full_prompt = f"{system_prompt}\n\n需要翻译的内容：\n{user_content}"
                response = self.gemini_model.generate_content(
                    full_prompt,
                    generation_config=import_genai().GenerationConfig(
                        temperature=0.3,
                        response_mime_type="application/json"
                    )
                )
                result_text = response.text
            else:
                # OpenAI 调用方式
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.3,
                    response_format={ "type": "json_object" } if "gpt-4" in self.model or "gpt-3.5" in self.model else None
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
            print(f"LLM 翻译发生错误: {e}")
            for i, bbox in enumerate(bboxes):
                if not bbox.get("translated_text"):
                    bbox["translated_text"] = "[翻译失败]"
            return bboxes

    def _mock_translation(self, bboxes):
        for i, bbox in enumerate(bboxes):
            bbox["translated_text"] = f"[占位: {bbox.get('original_text', '')}]"
        return bboxes

def import_genai():
    import google.generativeai as genai
    return genai
