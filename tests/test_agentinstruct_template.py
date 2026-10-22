import unittest

from workloads.build_agentinstruct import segments, turn_text


class QwenTemplateTests(unittest.TestCase):
    def test_turn_text_uses_qwen_chatml(self):
        self.assertEqual(turn_text("user", "hello"), "<|im_start|>user\nhello<|im_end|>\n")

    def test_segments_build_qwen_assistant_prompt(self):
        conversation = [
            {"from": "human", "loss": False, "value": "question"},
            {"from": "gpt", "loss": True, "value": "tool call"},
            {"from": "human", "loss": False, "value": "tool result"},
            {"from": "gpt", "loss": True, "value": "answer"},
        ]
        context, calls = segments(conversation)
        self.assertEqual(context, "<|im_start|>user\nquestion<|im_end|>\n<|im_start|>assistant\n")
        self.assertEqual(calls, [("tool call", "tool result"), ("answer", None)])


if __name__ == "__main__":
    unittest.main()
