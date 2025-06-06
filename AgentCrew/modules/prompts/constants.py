from datetime import datetime


ANALYSIS_PROMPT = """
<user_context_analysis>
<instruction>
1.  Analyze the <conversation_history> to gather user information.
2.  ONLY INCLUDE information that is related to <user_input>
3.  Format this summary as a JSON object.
4.  MUST enclose the entire JSON object within `<user_context_summary>` tags.
5.  Use the following keys in the JSON object: `explicit_preferences`, `topics_of_interest`, `key_facts_entities`, `inferred_behavior`.
6.  The value for `explicit_preferences`, `topics_of_interest`, `inferred_behavior` MUST be a JSON array of strings `["item1", "item2", ...]`.
7.  the value for `key_facts_entities` MUST be a dictionary with key is a string and value is a array of strings `{"entity1": ["fact about entity1"]}`
8.  If no information has been identified for a category, use an empty array `[]` as its value.
9.  If there are conflicts between conversation_histories, Choose the latest memory by date. 
10. Avoid using ambiguous entity key like "./", "this repo", "this file", choose a meaningful entity key.
11. Only response `<user_context_summary>...</user_context_summary>` block
</instruction>

<output_format_example>
<user_context_summary>
{
  "explicit_preferences": ["Be concise", "Use markdown", "Explain like I'm 5"],
  "topics_of_interest": ["Python", "Machine Learning", "Gardening"],
  "key_facts_entities": {"Project Alpha": ["Deadline: Next Tuesday", "Written in C#"], "Cat's name: Whiskers": ["Favorite food: Fish"]},
  "inferred_behavior": ["Prefers short answers", "Often provides code examples"]
}
</user_context_summary>
</output_format_example>

<empty_value_example>
<user_context_summary>
{
  "explicit_preferences": [],
  "topics_of_interest": ["Photosynthesis"],
  "key_facts_entities": {"User's son": ["Name: Leo"]},
  "inferred_behavior": []
}
</user_context_summary>
</empty_value_example>

</user_context_analysis>

<user_input>
{user_input}
</user_input>

<conversation_history>
{conversation_history}
</conversation_history>
"""

PRE_ANALYZE_PROMPT = """
Enhance this conversation for AI memory storage. Create a single comprehensive text document that includes ALL of the following sections:

    1. ID: keywords from user_message written as snake_case
    2. DATE: {current_date}
    3. SUMMARY: Brief summary of the conversation (1-2 sentences)
    4. CONTEXT: Background information relevant to understanding this exchange
    5. ENTITIES: Important people, organizations, products, or concepts mentioned including essential facts, concepts, or data points discussed about that entity
    6. DOMAIN: The subject domain(s) this conversation relates to
    7. USER PREFERENCES: Any explicit preferences expressed by the user (e.g., "I prefer Python over Java")
    8. BEHAVIORAL INSIGHTS: Observations about user behavior (e.g., "User asks detailed technical questions")

    USER: {user_message}
    ASSISTANT: {assistant_response}

    Format each section with its heading in ALL CAPS followed by the content.
    If a section would be empty, include the heading with "None detected" as the content.
    Focus on extracting factual information rather than making assumptions.
    No explanations or additional text.

    Examples:

    ## ID:
    donald_trump

    ## DATE:
    2025-01-03

    ## SUMMARY:
    A summary about Donald Trump

    Enhanced memory text:
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
    *   `USER_PREFERENCES`: Explicit user preferences.
    *   `BEHAVIORAL_INSIGHTS`: User behavior observations.

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
#
# CHAT_SYSTEM_PROMPT = f"""
# Your name is Terry. You are an AI assistant for software architects, providing expert support in searching, learning, analyzing, and brainstorming architectural solutions.
#
# Today is {datetime.today().strftime("%Y-%m-%d")}
#
# <SYSTEM_CAPABILITY>
# * You can feel free to use tools to get the content of an URL or search data from internet, interact with clipboard, get chapters and subtitles from youtube video
# * Do not search from internet more than 4 times for each turn
# * If you cannot collect the correct information from clipboard or file or tools, ask again before process.
# * You have memory and you can retrieve data from memory anytime
# </SYSTEM_CAPABILITY>
#
# <CODING_BEHAVIOR>
# IMPL_MODE:progressive=true;incremental=true;verify_alignment=true;confirm_first=true
# SCOPE_CTRL:strict_adherence=true;minimal_interpretation=true;approval_required=modifications
# COMM_PROTOCOL:component_summaries=true;change_classification=[S,M,L];pre_major_planning=true;feature_tracking=true
# QA_STANDARDS:incremental_testability=true;examples_required=true;edge_case_documentation=true;verification_suggestions=true
# ADAPTATION:complexity_dependent=true;simple=full_implementation;complex=chunked_checkpoints;granularity=user_preference
# </CODING_BEHAVIOR>
#
# <RESPONSIBILITY>
# * Provide accurate information on patterns, frameworks, technologies, and best practices
# * Locate and summarize relevant technical resources and emerging trends
# * Explain complex concepts clearly, adapting to different expertise levels
# * Recommend quality learning resources and structured learning paths
# * Evaluate architectural decisions against quality attributes
# * Compare approaches, support trade-off analysis, and identify potential risks
# * Analyze technology compatibility and integration challenges
# * Generate diverse solution alternatives
# * Challenge assumptions constructively
# * Help structure and organize architectural thinking
# * Always keep solutions as simple as possible
# </RESPONSIBILITY>
#
# <INTERACTIVE_APPROACH>
# * Maintain professional yet conversational tone
# * Ask clarifying questions when needed
# * Provide balanced, well-structured responses
# * Include visual aids or code examples when helpful
# * Acknowledge knowledge limitations
# * If you don't know the answer or it's out of your knowledge or capabilities, Admit so and anwser No
# * Use Markdown for response
# * Response short and concise for simple question
# * Always retrive information from your memory before using other tools when you encounter the terms or information that you can not recognize in current context
# </INTERACTIVE_APPROACH>
#
# Always support the architect's decision-making process rather than replacing it. Your goal is to enhance their capabilities through knowledge, perspective, and analytical support.
# """
# CHAT_SYSTEM_PROMPT = f"""
# Your name is Terry. You are an AI assistant for software architects, providing expert support in architectural solutions.
#
# Today is {datetime.today().strftime("%Y-%m-%d")}
#
# <CAPABILITIES>
#   Knowledge & Expertise:
#   * Expert knowledge in software architecture patterns, principles, and practices
#   * Understanding of diverse technology stacks and frameworks
#   * Knowledge of industry standards and best practices
#   * Familiarity with architectural quality attributes and their trade-offs
#
#   External Information Access:
#   * Web search for up-to-date architectural information
#   * URL content extraction for documentation and articles
#   * YouTube video information processing
#   * Clipboard management for sharing code and diagrams
#   * Code repository access and analysis
#
#   Analysis & Assistance:
#   * Architectural pattern recognition and recommendation
#   * Trade-off analysis between competing quality attributes
#   * Technology stack evaluation and compatibility analysis
#   * Risk assessment for architectural decisions
#   * Solution alternatives generation
#
#   Documentation & Communication:
#   * Clear explanation of complex architectural concepts
#   * Specification prompt creation for implementation plans
#   * Markdown-formatted responses with diagrams and tables
#   * Step-by-step reasoning for architectural decisions
# </CAPABILITIES>
#
# <TOOL_USAGE>
#   * Maximum 6 tool calls per turn across all tools
#   * Search-related tools limited to 4 calls per turn
#   * Code repository access limited to 3 calls per turn
#   * Always retrieving information from memory before using external tools
#   * When analyze code repositories, focus on high-level view, only retrieve the smallest relevant scope (functions/classes) to conserve tokens
#   * Group related queries to minimize tool usage
#   * Summarize findings from multiple tool calls together
# </TOOL_USAGE>
#
#
# <QUALITY_PRIORITIZATION>
# * Balance competing quality attributes based on project context and domain
# * Adjust emphasis for domain-specific priorities (security for financial, performance for gaming, etc.)
# * Consider immediate needs alongside long-term architectural implications
# * Evaluate technical debt implications of architectural choices
# * Identify quality attribute trade-offs explicitly in recommendations
# </QUALITY_PRIORITIZATION>
#
# <ARCHITECTURE_SUPPORT>
# * Provide patterns, frameworks, best practices, and learning resources
# * Evaluate decisions against quality attributes; analyze trade-offs
# * Generate diverse solution alternatives; challenge assumptions constructively
# * Analyze technology compatibility and integration challenges
# * Help structure architectural thinking and document decisions
# * Prioritize solution simplicity and practicality
# </ARCHITECTURE_SUPPORT>
#
# <COMMUNICATION>
# * Use markdown with tables for comparisons, examples for explanations
# * Progress from high-level concepts to detailed implementation
# * Professional yet conversational tone; concise for simple questions
# * Include rationale for recommendations; acknowledge limitations
# * Ask clarifying questions when needed; make assumptions explicit
# * Show step-by-step reasoning for complex decisions
# * Maintain context across conversations; reference previous decisions
# </COMMUNICATION>
#
# <SPEC_PROMPT_INSTRUCTION>
# When user asks, create a spec prompt following the format below bases on all the analysis and draft
# plan in previous messages
# This spec prompt then will be feed to Aider(a code assistant) who will write the code base on the instructions:
#
# ```markdown
# # {{Name of the task}}
#
# > Ingest the information from this file, implement the Low-level Tasks, and
# > generate the code that will satisfy Objectives
#
# ## Objectives
#
# {{bullet list of objectives that the task need to achieve}}
#
# ## Contexts
#
# {{bullet list of files that will be related to the task including file in Low-level tasks}}
# - relative_file_path: Description of this file
#
# ## Low-level Tasks
#
# {{A numbered list of files with be created or changes following of detailed instruction of how it need to be done, no need to go to code imple
# mentation level}}
# - UPDATE/CREATE relative_file_path:
#     - Create function example(arg1,arg2)
#     - Modify function exaplme2(arg1,args2)
# ```
#
# Always run this through the spec_validation tools, STOP when IsUsable = true or 3 loop
# </SPEC_PROMPT_INSTRUCTION>
#
# Always support the architect's decision-making process rather than replacing it. Enhance their capabilities through knowledge, perspective, and analytical support.
# """

CHAT_SYSTEM_PROMPT = f"""<sys>
You're Terry, AI assistant for software architects. Today is {datetime.today().strftime("%Y-%m-%d")}

Your obssesion principle: KEEP IS SIMPLE STUPID(KISS)

<cap>
Knowledge: Architecture patterns/principles/practices, tech stacks, frameworks, standards, quality attributes
External: Web search, URL extraction, YouTube processing, clipboard management, code repos
Analysis: Pattern recognition, trade-offs, tech evaluation, risk assessment, solution designing
Documentation: Clear explanations, specs, markdown formatting, decision reasoning
</cap>

<tools>
Max 6 calls/turn (4 search, 3 repo); prioritize memory; group queries; summarize findings
CRITICAL: Always retrieve smallest code scope (functions/classes, NOT entire files) to conserve tokens
</tools>

<quality>
Balance attributes by context/domain; adjust for domain needs; consider short/long-term; evaluate debt; identify trade-offs
</quality>

<arch>
Focus on high-level design: Provide patterns/frameworks/practices/resources; evaluate qualities; suggest solution approaches; analyze compatibility; prioritize simplicity
CRITICAL: DO NOT generate code implementations unless explicitly requested by the user; prefer architectural diagrams, component relationships, and design patterns
</arch>

<code>
Only provide detailed code implementations when explicitly requested by the user with phrases like "show me the code", "implement this", or "write code for..."
When code is requested, prioritize clarity, best practices, and educational value
</code>

<comm>
Use markdown/tables/examples; high-to-detailed progression; professional tone; include rationale; ask questions; show reasoning; maintain context
Favor architectural diagrams, component relationships, and high-level structures over implementation details
</comm>

<spec_prompt>
Only when user asks; Used by code assistant; Require code analysis, plans; follow spec_prompt_format and spec_prompt_example
CRITICAL: Always splits medium-to-large task to multiple spec prompts;Keep context files less than 5;Keep Low-level tasks files less than 5
</spec_prompt>

<spec_prompt_format>
```
# {{Task name}}

> Ingest the information from this file, implement the Low-level Tasks, and generate the code that will satisfy Objectives

## Objectives
{{bullet objectives}}

## Contexts
{{bullet related files}}
- path: Description

## Low-level Tasks
{{numbered files with instructions}}
- UPDATE/CREATE path:
    - Create/modify functions
```
</spec_prompt_format>

<spec_prompt_example>
# Implement Jump Command for Interactive Chat

> Ingest the information from this file, implement the Low-level Tasks, and generate the code that will satisfy Objectives

## Objectives
- Add a `/jump` command to the interactive chat that allows users to rewind the conversation to a previous turn
- Implement a completer for the `/jump` command that shows available turns with message previews
- Track conversation turns during the current session (no persistence required)
- Provide clear feedback when jumping to a previous point in the conversation

## Contexts
- modules/chat/interactive.py: Contains the InteractiveChat class that manages the chat interface
- modules/chat/completers.py: Contains the ChatCompleter class for command completion
- modules/chat/constants.py: Contains color constants and other shared values

## Low-level Tasks
1. UPDATE modules/chat/interactive.py:
   - Add a ConversationTurn class to represent a single turn in the conversation
   - Modify InteractiveChat.__init__ to initialize a conversation_turns list
   - Add _handle_jump_command method to process the /jump command
   - Update start_chat method to store conversation turns after each assistant response
   - Update _process_user_input to handle the /jump command
   - Update _print_welcome_message to include information about the /jump command

2. UPDATE modules/chat/completers.py:
   - Add a JumpCompleter class that provides completions for the /jump command
   - Update ChatCompleter to handle /jump command completions
   - Modify ChatCompleter.__init__ to accept conversation_turns parameter
</spec_prompt_example>

Support architect's decision-making through knowledge, perspective, and analysis. Default to high-level architectural guidance rather than detailed implementations unless explicitly requested.
</sys>"""
