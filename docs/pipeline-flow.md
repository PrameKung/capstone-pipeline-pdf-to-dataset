# Pipeline flow

The page-removal and rendering stage is implemented. The remaining steps are planned.

```mermaid
flowchart TD
    A[PDF in raw/] --> B[Remove page 1]
    A -. keep unchanged .-> A
    B --> C[Working PDF in .pipeline/]
    C --> D[Render pages to images]
    D --> E[Keep original page numbers]
    E --> F[Save untouched grading view]
    F --> H[Save original grading images]
    E --> I[Create student view<br/>red-channel grayscale]

    I --> K[Student extraction pass]
    H --> L[Teacher mark extraction pass]
    K --> M[Combine extracted records]
    L --> M
    M --> N[Format readable Markdown]
    N --> O[Map and validate CSV fields]
    O --> P[Write one CSV under output/]

```

The student pass receives only the red-channel grayscale student view, while the grading pass receives the untouched grading view. Red-ink detection and masking are not performed. Their results can be combined into a readable Markdown record, then mapped to the fixed CSV format. Keep untouched page renders as evidence and flag uncertain marks rather than guessing. Each PDF's inspectable working files are grouped by its path under `raw/`; for example, `raw/signal/318/1.pdf` uses `.pipeline/jobs/signal/318/1/`.

Example Markdown record for a question:

```md
## Question 1

### Student answer
x + 5 = 10
x = 10 - 5
x = 5

### Teacher mark
Raw mark: check symbol beside “x = 5”
Score: unclear
```
