"""
Test script: verify gpt-5.4-nano reasoning_effort via direct OpenAI
Checks that reasoning tokens appear in usage metadata.
"""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
MODEL = os.getenv("DEFAULT_AGENT_MODEL", "gpt-5.4-nano")

print(f"Model   : {MODEL}")
print(f"Base URL: {BASE_URL}")
print(f"Key     : {API_KEY[:20]}...{API_KEY[-6:] if API_KEY else 'MISSING'}")
print()


async def test_with_model_kwargs():
    """Same construct currently used in nodes.py (reasoning_effort in model_kwargs)."""
    print("=== Test 1: reasoning_effort in model_kwargs (current app behaviour) ===")
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        model = ChatOpenAI(
            model=MODEL,
            api_key=API_KEY,
            base_url=BASE_URL,
            model_kwargs={"stream_options": {"include_usage": True}, "reasoning_effort": None},
        )
        if w:
            for warning in w:
                print(f"  WARNING: {warning.message}")

    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="What is 2+2? Answer briefly."),
    ]
    try:
        response = await model.ainvoke(messages)
        usage = response.usage_metadata or {}
        print(f"  Content        : {response.content[:80]}")
        print(f"  Input tokens   : {usage.get('input_tokens')}")
        print(f"  Output tokens  : {usage.get('output_tokens')}")
        print(f"  Reasoning toks : {usage.get('input_token_details', {}).get('reasoning') or usage.get('output_token_details', {}).get('reasoning_tokens') or 'not present'}")
        print(f"  Full usage     : {usage}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()


async def test_with_explicit_param():
    """Correct way: reasoning_effort as explicit param, no stream_options for ainvoke."""
    print("=== Test 2: reasoning_effort as explicit param, ainvoke (channels/ai/nodes.py) ===")
    try:
        model = ChatOpenAI(
            model=MODEL,
            api_key=API_KEY,
            base_url=BASE_URL,
            reasoning_effort=None,
            model_kwargs={},
        )
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="What is 2+2? Answer briefly."),
        ]
        response = await model.ainvoke(messages)
        usage = response.usage_metadata or {}
        print(f"  Content        : {response.content[:80]}")
        print(f"  Input tokens   : {usage.get('input_tokens')}")
        print(f"  Output tokens  : {usage.get('output_tokens')}")
        print(f"  Reasoning toks : {usage.get('input_token_details', {}).get('reasoning') or usage.get('output_token_details', {}).get('reasoning_tokens') or 'not present'}")
        print(f"  Full usage     : {usage}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()


async def test_streaming():
    """Same streaming construct as llm_node in agents/graph/nodes.py."""
    print("=== Test 3: streaming (same as llm_node astream) ===")
    try:
        model = ChatOpenAI(
            model=MODEL,
            api_key=API_KEY,
            base_url=BASE_URL,
            reasoning_effort=None,
            model_kwargs={"stream_options": {"include_usage": True}},
        )
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            HumanMessage(content="What is 2+2? Answer briefly."),
        ]
        response = None
        async for chunk in model.astream(messages):
            response = chunk if response is None else response + chunk

        usage = response.usage_metadata or {}
        print(f"  Content        : {response.content[:80]}")
        print(f"  Input tokens   : {usage.get('input_tokens')}")
        print(f"  Output tokens  : {usage.get('output_tokens')}")
        print(f"  Reasoning toks : {usage.get('input_token_details', {}).get('reasoning') or usage.get('output_token_details', {}).get('reasoning_tokens') or 'not present'}")
        print(f"  Full usage     : {usage}")
    except Exception as e:
        print(f"  ERROR: {e}")


asyncio.run(test_with_model_kwargs())
asyncio.run(test_with_explicit_param())
asyncio.run(test_streaming())
