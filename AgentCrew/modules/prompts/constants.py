PRE_ANALYZE_PROMPT = """
<MEMORY_PROCESSING_REQUEST>
    Extract this conversation for AI memory storage. Create a comprehensive xml record that includes all fields in <OUTPUT_FORMAT> below.
    <OUTPUT_FORMAT>
    1. ID: meaningful id from summary written as snake_case.
    2. DATE: {current_date}
    3. SUMMARY: Brief summary of the conversation (3-6 sentences)
    4. CONTEXT: Background information relevant to understanding this exchange
    5. ENTITIES: Important people, organizations, products, or concepts mentioned including essential facts, concepts, or data points discussed about that entity
    6. DOMAINS: The subject domain(s) this conversation relates to
    8. CONVERSATION_NOTES: The key extracted information from conversation.
    </OUTPUT_FORMAT>

    <CONVERSATION_TURN>
        <USER>
        {user_message}
        </USER>
        <ASSISTANT>
        {assistant_response}
        </ASSISTANT>
    </CONVERSATION_TURN>

    <PROCESSING_INSTRUCTIONS>
    1. Format each section with its heading in ALL CAPS as a tag wrapped around the content.
    2. If a section would be empty, include the heading with "None detected" as the content.
    3. Focus on extracting factual information rather than making assumptions.
    4. <CONVERSATION_NOTES> should capture all the key points and the direction of flow of the whole conversation in concise.
    5. No explanations or additional text.
    </PROCESSING_INSTRUCTIONS>

    <EXAMPLES>
        <MEMORY>
            <ID>donald_trump</ID>
            <DATE>2025-01-03</DATE>
            <SUMMARY>A summary about Donald Trump</SUMMARY>
            <CONTEXT>Contact information, background, and other relevant details about Donald Trump</CONTEXT>
            <ENTITIES>
                <ENTITY>
                    <NAME>DONALP TRUMP</NAME>
                    <DESC>President of United States</DESC>
                </ENTITY>
            </ENTITIES>
            <DOMAINS>
                <DOMAIN>Politics</DOMAIN>
            </DOMAINS>
            <CONVERSATION_NOTES>
                <NOTE>User asked about Donald Trump's background. Assistant provided details on his presidency and key events.</NOTE>
            </CONVERSATION_NOTES>
        </MEMORY>
    </EXAMPLES>
</MEMORY_PROCESSING_REQUEST>
"""

PRE_ANALYZE_WITH_CONTEXT_PROMPT = """
<MEMORY_PROCESSING_REQUEST>
    Extract this conversation for AI memory storage. Create a comprehensive xml record following INSTRUCTIONS that includes all fields in <OUTPUT_FORMAT> below. No explanations or additional text.

    <INSTRUCTIONS>
    1. ID: use existed id from <PREVIOUS_CONVERSATION_CONTEXT> if available or create one.
    2. DATE: {current_date}
    3. SUMMARY: Merge the SUMMARY of <PREVIOUS_CONVERSATION_CONTEXT> with new CONVERSATION_TURN (3-6 sentences)
    4. CONTEXT: Merge the CONTEXT of <PREVIOUS_CONVERSATION_CONTEXT> with new context in CONVERSATION_TURN
    5. ENTITIES: Add to the ENTITIES of <PREVIOUS_CONVERSATION_CONTEXT> for new important people, organizations, products, or concepts mentioned in CONVERSATION_TURN including essential facts, concepts, or data points discussed about that entity
    6. DOMAINS: Add to the DOMAINS of <PREVIOUS_CONVERSATION_CONTEXT> for new subject domain(s) in CONVERSATION_TURN related
    7. CONVERSATION_NOTES: Add the CONVERSATION_NOTES of <PREVIOUS_CONVERSATION_CONTEXT> for new key notes extracted information from CONVERSATION_TURN. PREVIOUS_CONVERSATION_CONTEXT CONVERSATION_NOTES must be keep intact.
    </INSTRUCTIONS>

    {conversation_context}

    <CONVERSATION_TURN>
        <USER>
        {user_message}
        </USER>
        <ASSISTANT>
        {assistant_response}
        </ASSISTANT>
    </CONVERSATION_TURN>

    <OUTPUT_FORMAT>
        <MEMORY>
            <ID>[existed_id]</ID>
            <DATE>[current_date]</DATE>
            <SUMMARY>[merged_summary]</SUMMARY>
            <CONTEXT>[merged_context]</CONTEXT>
            <ENTITIES>
                <ENTITY>
                    <NAME>[added_entity_name]</NAME>
                    <DESC>[added_entity_description]</DESC>
                </ENTITY>
            </ENTITIES>
            <DOMAINS>
                <DOMAIN>[added_domain]</DOMAIN>
            </DOMAINS>
            <CONVERSATION_NOTES>
                <NOTE>[added_notes]</NOTE>
            </CONVERSATION_NOTES>
        </MEMORY>
    </OUTPUT_FORMAT>
</MEMORY_PROCESSING_REQUEST>
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
    *   `CONTEXT`: Background information.
    *   `ENTITIES`: Key people, orgs, products, concepts, facts.
    *   `DOMAIN`: Subject domain(s).

**Processing Instructions:**
1.  **Relevance Filtering:**
    *   Keep a snippet only if its `SUMMARY`, `CONTEXT`, or `ENTITIES` fields demonstrate clear and direct relevance to `INPUT_KEYWORDS`.
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
