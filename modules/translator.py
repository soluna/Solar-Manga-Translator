import json
import os
from openai import OpenAI
import time

class LLMTranslator:
    def __init__(self, api_key=None, model="gpt-4o"):
        """
        初始化大语言模型翻译器
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None
        
    def _build_prompt(self, text_list):
        """
        构建针对漫画翻译的系统提示词
        输入是当前页面上所有的文字气泡列表，以保证翻译具有上下文连贯性
        """
        prompt = """
你是一个专业的日本漫画汉化组翻译人员。
我将提供这一页漫画中提取出来的所有日文台词（按照从右到左、从上到下的阅读顺序排列）。
你需要将它们翻译成自然、流畅、符合中文读者阅读习惯的中文台词。

规则：
1. 请根据所有台词的上下文来推断说话者的语气、身份。
2. 对于拟声词、背景音效，可以翻译为简短的中文词（如"咚"、"啪嗒"）。
3. 如果原文看起来像是 OCR 识别错误的乱码（没有意义），你可以尝试纠正，或者直接返回空字符串。
4. **必须且只能**返回一个包含翻译结果的 JSON 数组。数组的长度和顺序必须与输入完全一致。

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
        # 将用户的文字包装为 JSON
        user_input = []
        for i, text in enumerate(text_list):
            user_input.append({"id": i, "text": text})
            
        return prompt, json.dumps(user_input, ensure_ascii=False)

    def translate_batch(self, bboxes):
        """
        批量翻译当前页面的所有对话框
        输入 bboxes 为包含 'original_text' 键的字典列表
        """
        if not self.client:
            print("未提供 API Key，无法进行 LLM 翻译。")
            # 伪造翻译用于离线测试
            for i, bbox in enumerate(bboxes):
                bbox["translated_text"] = f"[翻译占位符: {bbox.get('original_text', '')}]"
            return bboxes

        # 1. 收集需要翻译的非空文本
        texts_to_translate = []
        mapping = {} # id 到 bbox 索引的映射
        
        current_id = 0
        for i, bbox in enumerate(bboxes):
            jp_text = bbox.get("original_text", "").strip()
            if jp_text: # 忽略空字符串
                texts_to_translate.append(jp_text)
                mapping[current_id] = i
                current_id += 1
            else:
                bbox["translated_text"] = ""

        if not texts_to_translate:
            return bboxes

        # 2. 构建 Prompt 并请求 LLM
        system_prompt, user_content = self._build_prompt(texts_to_translate)
        
        try:
            print(f"正在使用 {self.model} 翻译 {len(texts_to_translate)} 句台词...")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.3, # 偏向于稳定准确的翻译
                response_format={ "type": "json_object" } if "gpt-4" in self.model or "gpt-3.5" in self.model else None
            )
            
            result_text = response.choices[0].message.content
            print(f"LLM 原始返回: {result_text}")
            
            # 解析 JSON 数组
            # Handle both formats: direct array or wrapped in an object if forced JSON object
            parsed_data = json.loads(result_text)
            if isinstance(parsed_data, dict):
                # If wrapped in dict like {"translations": [...]}
                for key in parsed_data:
                    if isinstance(parsed_data[key], list):
                        parsed_data = parsed_data[key]
                        break

            # 3. 将翻译结果写回 bboxes
            for item in parsed_data:
                item_id = item.get("id")
                translation = item.get("translation", "")
                
                if item_id in mapping:
                    original_idx = mapping[item_id]
                    bboxes[original_idx]["translated_text"] = translation
                    
            return bboxes
            
        except Exception as e:
            print(f"LLM 翻译发生错误: {e}")
            # 出错时保留原文或者填充占位符
            for i, bbox in enumerate(bboxes):
                if not bbox.get("translated_text"):
                    bbox["translated_text"] = "[翻译失败]"
            return bboxes
