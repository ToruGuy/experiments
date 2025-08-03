# Daily News Agent - An Experiment

This project is an experimental news-gathering agent. It was built from scratch using `openrouter/horizon-beta` to generate the Python code over 9 iterative prompts. The model successfully generated and refined the script without a single error, demonstrating its strong code-generation capabilities.

The agent uses a multi-LLM approach via the OpenRouter API to perform intelligent, topic-based web searches.

## Setup

1.  **Create a virtual environment and install dependencies using `uv`:**
    ```bash
    uv venv
    source .venv/bin/activate
    uv pip install -r requirements.txt
    ```

2.  **Set up your environment variables:**
    Create a `.env` file from the example:
    ```bash
    cp .env.example .env
    ```
    Open the `.env` file and add your OpenRouter API key:
    ```
    OPENROUTER_API_KEY=your_openrouter_api_key_here
    ```

## How to Run

The script is executed from the command line with a required topic and several optional parameters to control its behavior.

**Full Usage:**

```bash
python search_component.py <topic> [--depth <max_depth>] [--window <hours>] [--output <dir>] [--limit <num>]
```

**Arguments:**

-   `topic`: The main topic to search for (e.g., "AI advancements"). **(Required)**
-   `--depth` or `-d`: Maximum number of "deepen" cycles. Default: `2`.
-   `--window` or `-w`: Time window in hours to consider articles. Default: `48`.
-   `--output` or `-o`: Directory to save the final JSON result. Default: `output`.
-   `--limit` or `-l`: Max number of items to return in the final result. Default: `10`.

**Example:**

```bash
python search_component.py "Polish stock market" --depth 3 --window 72 --limit 15
```

## How It Works

The agent's logic is orchestrated by `search_component.py`:

1.  **Plan**: Uses `openrouter/horizon-alpha` to generate initial search queries based on the topic.
2.  **Search**: Executes the search queries using `openai/gpt-4o-mini-search-preview`.
3.  **Filter**: Normalizes URLs, filters by recency (the `--window` parameter), and checks sources against a topic-specific whitelist.
4.  **Decide**: Presents a preview of findings to `openrouter/horizon-beta`, which decides to either `stop` or `deepen` the search by generating more targeted queries.
5.  **Rank & Finalize**: If the search is stopped, the collected articles are ranked based on source quality, recency, and relevance. The top results (up to the `--limit`) are saved to a JSON file in the output directory.

## Customization

To adapt the agent, edit `search_component.py`:

-   **Topics & Sources**: Modify `topic_whitelist()` to manage topics and their trusted domains.
-   **Source Ranking**: Adjust the `HIGH_TIER` and `MEDIUM_TIER` sets to change source credibility.
-   **Ranking Logic**: Tweak the scoring functions like `rank_basic()` to alter how results are prioritized.
