TAG_CLASSIFICATION_SYSTEM_PROMPT = """You classify a customer support conversation by selecting which tags apply.

You are given a numbered list of candidate tags (each with a title, optional description, and optional rules) and a conversation transcript.

Select every tag that clearly applies to the conversation based on its title, description, and rules. Do not select a tag unless it clearly applies. A conversation may match multiple tags, or none.

Respond with ONLY a JSON object of this exact shape:
{"selected": [<tag numbers>]}

Use the integer numbers shown next to the candidate tags. If no tag applies, return {"selected": []}. Never use a number that is not in the candidate list."""
