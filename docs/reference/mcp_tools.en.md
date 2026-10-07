<!-- translation-of: docs/reference/mcp_tools.md sha256:5504138846ef -->
**English** · [Русский](mcp_tools.md)

# Health OS MCP tools

What the MCP server (`health_mcp.py`, tools in `mcp_tools.py`) gives to cloud assistants.
Connecting — [how-to](../how-to/connect_cloud_assistants.md), intent — [explanation](../explanation/mcp_gateway.md).

## Common to all tools

| Property | How it works |
|---|---|
| Writing | None. Every tool only reads, through the existing `*_db` functions |
| Whose data | The process data directory (`HEALTH_DATA_DIR`); there is no "tenant" argument |
| Answer head | `Источник: Health OS — <what>. Выдано <YYYY-MM-DD>.` (source and issue date), then JSON |
| Emptiness | `нет данных: <what>. Не достраивай значения…` — "no data, do not fill in values" |
| Bad argument | `неверный аргумент: <what is wrong>`, MCP error flag |
| Read failure | "Health OS could not read the data — internal error, no data in the answer", no trace |
| Personal | Name, date of birth and identifiers are cut by the dictionary (`[скрыто]`); document paths are not returned |

## Tools

| Name | Arguments | Returns |
|---|---|---|
| `ping` | — | "Health OS на связи." No health data |
| `health_brief` | — | The same brief about the person that the bot receives before every answer: current belief, age, diagnosis and status, treatment with dates, current problems, recent labs with flags, personal HRV norm. The assistant is told to start with it |
| `nutrition_frame` | — | The nutrition frame derived by code from curated rules: energy, protein, micronutrients, constraints, the reason for each item. Separately, consilium rules in shadow status, each marked "NOT approved by the owner" |
| `lab_tests` | — | List of lab tests: name, date of the last result, number of results (over 20 years) |
| `lab_results` | `test_name` (string ≤ 100), `days` (integer, default 1825, ceiling 7300) | Series of one test: collection date, value, unit, reference from the form |
| `day_metrics` | `date` (`YYYY-MM-DD`) | Device metrics of one day as Health OS stores them |
| `problems` | `status`: `current` (default — current problems in any monitoring status), `resolved`, `all` | Problem list: title, status, dates, short description |
| `facts` | — | Long-term memory facts: key, value, since when. One-off (transient) facts are not returned |

## `lab_results`: substance, not name

The series is first assembled by substance (LOINC): different spellings and scales of one test are merged.
The answer has `by` (how the series was assembled), `names_merged` (which names were merged), `points`
(up to 500), `not_merged` (rows that could not be merged). When the substance is not found, the series is
assembled by the exact name, and the answer says so: "exact name, WITHOUT merging substances … the series
may be incomplete".

Limit: the reference is returned as it is stored. If a norm is stored in a different scale than the value,
the assistant sees that mismatch as is.
