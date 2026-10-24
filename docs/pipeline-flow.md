# Pipeline flow

The page-removal and rendering stage is implemented. The remaining steps are planned.

```mermaid
flowchart TD
    A[PDF in raw/] --> B[Remove page 1]
    A -. keep unchanged .-> A
    B --> C[Working PDF in .pipeline/]
    C --> D[Render pages to images]
    D --> E[Keep original page numbers]
    E --> F[Create student view<br/>suppress likely red marks]
    E --> G[Create grading view<br/>preserve original colors]

    F --> H[Student extraction pass]
    G --> I[Teacher mark extraction pass]
    H --> J[Combine extracted records]
    I --> J
    J --> K[Format readable Markdown]
    K --> L[Map and validate CSV fields]
    L --> M[Write one CSV under output/]

    N[Opposite-page red bleed-through] -. compare paired page evidence .-> F
    N -. flag ambiguous marks .-> I
```

The extraction passes use separate inputs: the student pass receives only the student view, while the grading pass receives the grading view. Their results can be combined into a readable Markdown record, then mapped to the fixed CSV format. Keep the untouched page renders as evidence and flag uncertain marks rather than guessing.

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
