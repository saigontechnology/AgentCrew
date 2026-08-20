# Instructions injected when there IS previous conversation context (merge mode)
MERGE_INSTRUCTIONS = """When PREVIOUS_CONVERSATION_CONTEXT is provided:
- Update HEAD to reflect the full conversation so far
- Merge FLOW with new information from CONVERSATION_TURN: combine INTENT if the topic shifted, extend EXPLORATION with new steps discussed, update OUTCOME with the latest conclusion or resolution
- Append new INSIGHTS, ENTITIES, DOMAINS, RESOURCES from CONVERSATION_TURN
- Keep ALL existing CONVERSATION_NOTES intact, append new ones from this turn"""

# Instructions injected when there is NO previous context (first turn)
FIRST_TURN_INSTRUCTIONS = """Create a new memory record from this conversation turn."""

PRE_ANALYZE_PROMPT = """
Extract this conversation into an XML <MEMORY> record.
Date: {current_date}
{context_instructions}
{conversation_context}
<CONVERSATION_TURN>
<USER>
{user_message}
</USER>
<ASSISTANT>
{assistant_response}
</ASSISTANT>
</CONVERSATION_TURN>

Rules:
- Output ONLY the <MEMORY> XML block. No other text.
- Use "" for any field with nothing relevant.
- FLOW: capture how the conversation progressed — what the user asked (INTENT), what was explored or discussed (EXPLORATION), and what was concluded or left open (OUTCOME). This is NOT a flat summary; it must show the dialogue arc from question to resolution.
- CONVERSATION_NOTES: capture ONLY actionable notes for future reference — caveats, edge cases, workarounds (note what they fix and when removable), non-obvious decisions with rationale, constraints, unresolved issues, user corrections/preferences.

<EXAMPLE>
<MEMORY>
    <HEAD>debugging async streaming issue in task_manager</HEAD>
    <DATE>2025-06-15</DATE>
    <FLOW>
        <INTENT>User asked for help debugging a race condition in async generator streaming where task state was being mutated during iteration</INTENT>
        <EXPLORATION>Agent identified that the task state dict was being mutated while an async generator was yielding from it, causing RuntimeError. Explored using asyncio.Lock as a synchronization workaround and discussed deep-copying state before yielding.</EXPLORATION>
        <OUTCOME>Added asyncio.Lock around state access as a temporary fix. Identified an unresolved memory leak when clients disconnect mid-stream because generator cleanup is not triggered reliably.</OUTCOME>
    </FLOW>
    <INSIGHTS>
        <INSIGHT>Async generators in Python need explicit cleanup — relying on GC causes resource leaks in long-running services</INSIGHT>
    </INSIGHTS>
    <ENTITIES>
        <ENTITY>
            <NAME>task_manager.py</NAME>
            <DESC>Core module handling task lifecycle and async streaming, ~800 lines</DESC>
        </ENTITY>
    </ENTITIES>
    <DOMAINS>
        <DOMAIN>Software Development</DOMAIN>
        <DOMAIN>Async Programming</DOMAIN>
    </DOMAINS>
    <RESOURCES>
        <RESOURCE>AgentCrew/modules/a2a/task_manager.py</RESOURCE>
    </RESOURCES>
    <CONVERSATION_NOTES>
        <NOTE>Caveat: task state dict must not be mutated while an async generator is yielding from it — causes RuntimeError. Deep copy before yielding.</NOTE>
        <NOTE>Workaround: added asyncio.Lock around state access — temporary fix until task state is refactored to immutable snapshots.</NOTE>
        <NOTE>Unresolved: memory leak when client disconnects mid-stream — generator cleanup not triggered reliably.</NOTE>
    </CONVERSATION_NOTES>
</MEMORY>
</EXAMPLE>
"""

POST_RETRIEVE_MEMORY = """
<INPUT_KEYWORDS>
{keywords}
</INPUT_KEYWORDS>
<MEMORY_LIST>
{memory_list}
</MEMORY_LIST>

**Task:** As an AI data processor, filter and clean timestamped conversation memory snippets based on `INPUT_KEYWORDS`.

**Goal:** Output a cleaned list of memory snippets that are:
1.  **Relevant:** Directly relevant to the provided `INPUT_KEYWORDS`.
2.  **Current & Accurate:** Resolve conflicts using the `DATE` field, prioritizing newer entries.
3.  **Noise-Free:** Eliminate irrelevant or only vaguely related snippets.

**Input Provided:**
1.  `INPUT_KEYWORDS`: A string of keywords defining the topic of interest.
2.  `MEMORY_LIST`: A list of memory snippet objects. Each object includes:
    *   `ID`: Unique identifier.
    *   `DATE`: "YYYY-MM-DD" format.
    *   `SUMMARY`: Brief summary.
    *   `FLOW`: Conversation progression — INTENT (what was asked), EXPLORATION (what was discussed/tried), OUTCOME (what was concluded).
    *   `ENTITIES`: Key people, orgs, products, concepts, facts.
    *   `DOMAIN`: Subject domain(s).

**Processing Instructions:**
1.  **Relevance Filtering:**
    *   Keep a snippet only if its `SUMMARY`, `FLOW`, or `ENTITIES` fields demonstrate clear and direct relevance to `INPUT_KEYWORDS`.
    *   Discard snippets that are off-topic, tangentially related, or lack substantial information regarding `INPUT_KEYWORDS`.
2.  **Recency and Conflict Resolution (Prioritize Newer):**
    *   When multiple relevant snippets address the *exact same specific fact/entity* related to `INPUT_KEYWORDS`: Retain the snippet with the most recent `DATE` and discard older ones if they present outdated or directly conflicting information on that specific point.
    *   If relevant snippets discuss *different aspects* or details related to `INPUT_KEYWORDS` and do not directly conflict, they can all be kept if they pass relevance. Do not discard older snippets if they offer unique, still-relevant information not in newer ones.
3.  **Noise Reduction:**
    *   After the above filters, review and discard any remaining snippets that technically match keywords but add no real value or insight (e.g., a mere mention without substance).

**Output Format:**
*   Return a Markdown result containing only the filtered and cleaned memory snippets.
*   Snippets in the output should retain their original structure.
*   Maintain the original relative order or order chronologically by `DATE` (oldest relevant to newest relevant).

**Example Scenario:**
If `INPUT_KEYWORDS` = "Qwen3 model capabilities" and `MEMORY_LIST` contains:
*   A (`DATE`: "2024-05-01", `SUMMARY`: "Qwen3's context window size.")
*   B (`DATE`: "2025-03-10", `SUMMARY`: "Qwen3's updated context window.")
*   C (`DATE`: "2025-01-15", `SUMMARY`: "General LLM context, Qwen2 mentioned.")
*   D (`DATE`: "2025-03-11", `SUMMARY`: "Qwen3 coding abilities.")

Processing: Snippet C might be discarded (tangential). Snippet A is older; if B supersedes A's info on the *same point* (context window), A is discarded. Snippet D discusses a different capability and is relevant, so B and D would likely be kept.

**Primary Objective:** Distill `MEMORY_LIST` into a concise, relevant, and up-to-date set of information based on `INPUT_KEYWORDS`.
"""

CONSOLIDATION_PROMPT = """
You are a memory consolidation system for agent "{agent_name}".
Current date: {current_date}

A new memory was just stored:
<CURRENT_MEMORY>
{current_memory}
</CURRENT_MEMORY>

These existing memories were found to be semantically related:
{existing_memories}

Analyze the relationship between the current memory and each existing memory.
For each existing memory, decide ONE action:

- KEEP: Memory contains unique information not covered by the current memory. Leave it alone.
- MERGE: Memory overlaps significantly with current memory. Produce a merged version.
- DISCARD: Memory is fully superseded, outdated, or contradicted by the current memory.

Rules:
- When merging, combine insights, entities, notes from both into one clean <MEMORY> block
- When merging FLOW sections: combine INTENT if topics overlap, merge EXPLORATION steps chronologically, and let the latest OUTCOME take precedence while preserving any still-relevant earlier conclusions
- Convert any relative dates ("yesterday", "last week") to absolute dates
- Drop conversation notes that are generic summaries with no actionable value
- Preserve architecture decisions, user corrections, active workarounds, unresolved issues
- If merging, the merged result replaces BOTH the current memory and the merged candidate
- If multiple memories need merging, each successive MERGED_MEMORY must include information from ALL previously merged candidates (build cumulatively, not independently)
- If nothing needs merging or discarding, mark all as KEEP

Output format — output ONLY this XML, no other text:
<CONSOLIDATION_RESULT>
  <ACTION id="existing_memory_id_1" type="KEEP|MERGE|DISCARD"/>
  <ACTION id="existing_memory_id_2" type="KEEP|MERGE|DISCARD"/>
  ...
  <MERGED_MEMORY for="existing_memory_id">
    <MEMORY>
      ...full merged XML...
    </MEMORY>
  </MERGED_MEMORY>
</CONSOLIDATION_RESULT>
"""

SEMANTIC_EXTRACTING = """
Extract the core information from the user's message and generate a short sentence or phrase summarizing the main idea or context with key entities. No explanations or additional text
User input: {user_input}"""

# Prompt templates
EXPLAIN_PROMPT = """
Please explain the following markdown content in a way that helps non-experts understand it better.
Break down complex concepts and provide clear explanations.
At the end, add a "Key Takeaways" section that highlights the most important points.

Content to explain:
{content}
"""

SUMMARIZE_PROMPT = """
# Web Content Extraction and Compression

I'll provide you with raw HTML or text content from a web page. Your task is to process this content to extract and preserve only the essential information while significantly reducing the token count. Follow these steps:

## 1. Content Analysis
- Identify the main content sections of the page (articles, key information blocks)
- Distinguish between primary content and supplementary elements (navigation, ads, footers, sidebars)
- Recognize important structural elements (headings, lists, tables, key paragraphs)
- Identify code blocks and code examples that are relevant to the content

## 2. Extraction Process
- Remove all navigation menus, ads, footers, and sidebar content
- Eliminate redundant headers, copyright notices, and boilerplate text
- Preserve headings (H1, H2, H3) as they provide structural context
- Keep lists and tables but format them concisely
- Maintain critical metadata (publication date, author) if present
- Preserve ALL code blocks and programming examples in their entirety

## 3. Content Compression
- Remove unnecessary adjectives and filler words while preserving meaning
- Condense long paragraphs to their essential points
- Convert verbose explanations to concise statements
- Eliminate redundant examples while keeping the most illustrative ones
- Merge similar points into unified statements
- NEVER compress or modify code blocks - maintain them exactly as they appear

## 4. Special Content Handling
- For educational/technical content: preserve definitions, formulas, and key examples
- For news articles: maintain the 5W1H elements (Who, What, When, Where, Why, How)
- For product pages: keep specifications, pricing, and unique features
- For documentation: retain procedure steps, warnings, and important notes
- For technical/programming content: keep ALL code snippets, syntax examples, and command-line instructions intact

## 5. Output Format
- Present content in a structured, hierarchical format
- Use markdown for formatting to maintain readability with minimal tokens
- Include section headers to maintain document organization
- Preserve numerical data, statistics, and quantitative information exactly
- Maintain code blocks with proper markdown formatting (```language ... ```)
- Ensure inline code is preserved with backtick formatting (`code`)

Return ONLY the processed content without explanations about your extraction process. Focus on maximizing information retention while minimizing token usage.

WEB CONTENT: {content}
"""

BEHAVIOR_EXTRACTION_PROMPT = """
You are a behavior extraction system for agent "{agent_name}".
Current date: {current_date}

Analyze the following conversation turn and extract any potential adaptive behaviors — reusable patterns, user preferences, communication style cues, or successful task approaches that should be consistently applied in future interactions.

<CONVERSATION_TURN>
<USER>
{user_message}
</USER>
<ASSISTANT>
{assistant_response}
</ASSISTANT>
</CONVERSATION_TURN>

{existing_behaviors}

Extraction Guidelines:
- A behavior candidate is a "when [condition], do [actions]" rule that the agent should follow in future conversations.
- Look for: user preferences (communication style, output format, tool usage), successful problem-solving patterns, repeated workflows, corrections the user made, or explicit instructions.
- Do NOT extract one-time facts, project-specific trivia, or conversation summaries — only reusable behavioral rules.
- If a candidate overlaps with an existing behavior listed above, reuse the same ID to update it rather than creating a new one.
- If no behaviors can be extracted, output an empty <BEHAVIOR_CANDIDATES></BEHAVIOR_CANDIDATES> block.

Confidence Scoring (1-10):
- 9-10: User explicitly stated a preference or instruction (e.g., "always use uv", "don't add comments").
- 8: Strong implicit signal — user corrected the agent, expressed satisfaction with a specific approach, or a clear repeated pattern emerged.
- 6-7: Moderate signal — a plausible preference but not strongly confirmed.
- 1-5: Weak or speculative — not enough evidence to justify a persistent behavior.

Scope:
- "global": Applies to all conversations regardless of project.
- "project": Applies only to the current project context (e.g., project-specific conventions, tooling choices).

ID Convention:
- Use structured IDs: category_context (e.g., communication_style_technical, task_execution_code_review, personalization_output_format).

Output format — output ONLY this XML, no other text:
<BEHAVIOR_CANDIDATES>
  <CANDIDATE>
    <ID>category_context</ID>
    <CONDITION>triggering condition description</CONDITION>
    <ACTIONS>
      <ACTION>first action step</ACTION>
      <ACTION>second action step</ACTION>
    </ACTIONS>
    <SCOPE>global|project</SCOPE>
    <CONFIDENCE>1-10</CONFIDENCE>
  </CANDIDATE>
  ...
</BEHAVIOR_CANDIDATES>
"""

SCHEMA_ENFORCEMENT_PROMPT = """
<OUTPUT_SCHEMA_ENFORCEMENT>
<Instruction>
You MUST format your response STRICTLY according to the following JSON schema.
Do NOT include any text before or after the JSON output.
Your entire response should be valid JSON that conforms to this schema:
</Instruction>

<schema>
{schema_json}
</schema>

<Requirements>
1. Output ONLY valid JSON - no markdown, no explanations, no additional text
2. Follow the exact structure defined in the schema above
3. Respect all required fields, types, and constraints
4. Ensure all property names match exactly (case-sensitive)
5. Do not add extra fields not defined in the schema
</Requirements>
</OUTPUT_SCHEMA_ENFORCEMENT>
"""
