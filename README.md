# Shopping Plugin AI

Standalone AI foundation for a shopping Chrome extension. Start in [`ai/`](ai/README.md).

- Python 3.11+ package with a transport-independent `AIRequest → AIResponse` interface.
- Offline mock demo and an optional real Gemini structured-output adapter.
- Grounded product facts, clarification, and validated next-tool proposals.
- No backend, Neon connection, Chrome executor, STT or TTS wired yet.

## Try it

From the repository root, inside an activated Python virtual environment:

```sh
python -m pip install -e './ai[dev]'
python -m shopping_ai.cli ai/examples/product_question.json --fresh-demo
python -m shopping_ai.cli ai/examples/cart_proposal.json --fresh-demo
python -m pytest ai/tests -q
```

The mock is deterministic test/demo logic, not a language model. The cart example
returns a proposal; it does not click a page or claim a purchase/cart update.
For real model setup and the future backend contract, see [the AI README](ai/README.md).
