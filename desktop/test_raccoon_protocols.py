import asyncio
import json
import unittest

from raccoon_protocols import adapted_stream, from_chat, to_chat


class RaccoonProtocolTests(unittest.TestCase):
    def test_responses_tool_round_trip(self):
        payload = {"input": "call echo", "tools": [{"type": "function", "name": "echo",
                   "parameters": {"type": "object", "properties": {}}}], "tool_choice": "required"}
        chat, messages = to_chat("/v1/responses", payload, "raccoon-090dd3")
        self.assertEqual(chat["tools"][0]["function"]["name"], "echo")
        first = {"choices": [{"message": {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}]},
                 "finish_reason": "tool_calls"}]}
        response = from_chat("/v1/responses", first, "raccoon-090dd3@raccoon", messages)
        self.assertEqual(response["output"][0]["call_id"], "call_1")
        second, _ = to_chat("/v1/responses", {"previous_response_id": response["id"],
                            "input": [{"type": "function_call_output", "call_id": "call_1", "output": "42"}]}, "raccoon-090dd3")
        self.assertEqual(second["messages"][-1], {"role": "tool", "tool_call_id": "call_1", "content": "42"})

    def test_anthropic_image_and_tool_round_trip(self):
        body = {"model": "raccoon-090dd3", "messages": [
            {"role": "user", "content": [{"type": "text", "text": "color"},
              {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "tool_1", "name": "echo", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_1", "content": "42"}]}],
            "tools": [{"name": "echo", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "echo"}}
        chat, _ = to_chat("/v1/messages", body, "raccoon-090dd3")
        self.assertEqual(chat["messages"][0]["content"][1]["image_url"]["url"], "data:image/png;base64,AA==")
        self.assertEqual(chat["messages"][-1]["tool_call_id"], "tool_1")
        self.assertEqual(chat["tool_choice"]["function"]["name"], "echo")
        reply = from_chat("/v1/messages", {"choices": [{"message": {"role": "assistant", "content": "42"},
                          "finish_reason": "stop"}]}, "raccoon-090dd3@raccoon", [])
        self.assertEqual(reply["content"][0]["text"], "42")

    def test_fragmented_streams_finish_in_target_protocol(self):
        async def run(path):
            raw = (b'data: {"model":"raccoon-090dd3","choices":[{"delta":{"content":"Hi"}}]}\n\n'
                   b'data: {"model":"raccoon-090dd3","choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                   b'data: [DONE]\n\n')
            async def remainder():
                for part in (raw[20:30], raw[30:73], raw[73:]):
                    yield part
            data = b"".join([x async for x in adapted_stream(path, raw[:20], remainder(),
                                   "raccoon-090dd3", "raccoon-090dd3@raccoon", [{"role": "user", "content": "hello"}])])
            return data.decode()
        responses = asyncio.run(run("/v1/responses"))
        messages = asyncio.run(run("/v1/messages"))
        self.assertIn("event: response.output_text.delta", responses)
        self.assertIn("event: response.completed", responses)
        self.assertIn("event: content_block_delta", messages)
        self.assertIn("event: message_stop", messages)


if __name__ == "__main__":
    unittest.main()
