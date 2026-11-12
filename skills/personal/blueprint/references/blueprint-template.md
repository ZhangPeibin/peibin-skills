# Feature Blueprint: <feature name>

<At most five lines: who triggers it and from where; the user-visible outcome; the
key contract; failure and idempotency behavior; non-goals.>

```mermaid
%%{init: {
  "theme": "base",
  "look": "handDrawn",
  "themeVariables": {
    "background": "#FBF8F2",
    "primaryColor": "#F7F0E3",
    "primaryTextColor": "#2D2A26",
    "primaryBorderColor": "#B86B45",
    "lineColor": "#6F7C86",
    "secondaryColor": "#E8F0F5",
    "tertiaryColor": "#EDE9F7",
    "fontFamily": "ui-rounded, Trebuchet MS, sans-serif"
  }
}}%%
flowchart TB
    subgraph Client["App"]
        UI["Interaction"]
        State["State / provider"]
        Repo["API client / repository"]
    end

    subgraph Edge["API"]
        Route["Endpoint"]
        Auth["Auth + validation"]
    end

    subgraph Core["Service · Domain"]
        Service["Feature service"]
        Rules["Domain rules"]
    end

    subgraph Data["Persistence"]
        DB[("Store")]
    end

    UI -->|"user intent"| State
    State -->|"request DTO"| Repo
    Repo -->|"call"| Route
    Route --> Auth
    Auth -->|"validated input"| Service
    Service --> Rules
    Service -->|"write / read"| DB
    Service -->|"result"| Route
    Route -->|"status + body"| Repo
    Repo -->|"parsed result"| State
    State -->|"render"| UI

    classDef warm fill:#F7F0E3,stroke:#B86B45,stroke-width:1.7px,color:#2D2A26
    classDef blue fill:#E8F0F5,stroke:#456A7C,stroke-width:1.7px,color:#243746
    classDef sage fill:#EAF0E5,stroke:#6F8A62,stroke-width:1.7px,color:#2C3828
    classDef lavender fill:#EDE9F7,stroke:#7A6AA6,stroke-width:1.7px,color:#30294A
    classDef peach fill:#F8E8DF,stroke:#B86B45,stroke-width:1.7px,color:#3A2820
    class UI,State,Repo,Route,Auth warm
    class Service,Rules sage
    class DB blue
    style Client fill:#FBF8F2,stroke:#B86B45,stroke-width:1.5px
    style Edge fill:#FBF8F2,stroke:#B86B45,stroke-width:1.5px
    style Core fill:#FBF8F2,stroke:#6F8A62,stroke-width:1.5px
    style Data fill:#FBF8F2,stroke:#456A7C,stroke-width:1.5px
```

Replace the sample with the real flow. Delete layers and boxes that do not apply,
and add optional `AI`, `Async`, or `Media` subgraphs only when they exist. Use
`references/diagram-style.md` and `to-mermaid` for labels with paths or symbols.

Add a second sequence diagram only when order, retries, or async handoff cannot be
read from the flowchart. Do not add section headings, long tables, or a written
walkthrough.
