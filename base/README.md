# BnyCapstoneCrew Crew

Welcome to the BnyCapstoneCrew Crew project, powered by [crewAI](https://crewai.com). This template is designed to help you set up a multi-agent AI system with ease, leveraging the powerful and flexible framework provided by crewAI. Our goal is to enable your agents to collaborate effectively on complex tasks, maximizing their collective intelligence and capabilities.

## Installation

Ensure you have Python >=3.10 <3.13 installed on your system. This project uses [UV](https://docs.astral.sh/uv/) for dependency management and package handling, offering a seamless setup and execution experience.

First, if you haven't already, install uv:

```bash
pip install uv
```

Next, in the BNY_Crew_FOMC_Simulator directory run 
```bash
pip install -r requirements.txt
```
**DO NOT CREATE YOUR OWN VIRTUAL ENVIRONMENT**

Enter the bny_capstone_crew directory and lock the dependencies and install them by using the CLI command:
```bash
crewai install
```

**Add your `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, etc into the `.env` file**


## Running the Project

To kickstart your crew of AI agents and begin task execution, run this from the bny_capstone_crew directory:

```bash
$ crewai run
```

This command initializes the bny_capstone_crew Crew, assembling the agents and assigning them tasks as defined in your configuration.

This example, unmodified, will run the create a `report.md` file with the output of a research on LLMs in the root folder.

## Backtesting a meeting
Once you have successfully completed a single run, you may automate 5 runs of a given meeting. **Ensure that you update crew.py and Automated_metrics.py to have the correct meeting date and correct_vote variable.**

run this from the bny_capstone_crew directory:
```bash
$ chmod +x run_crew.sh
```
and any time you change any scripts, rerun the above CLI before running:

```bash
$ ./run_crew.sh
```

**The output will be in results.txt, but check all 5 summary json files to ensure formatting**


## Here are three consolidated FOMC voting members that encapsulate the personalities and decision-making patterns of the original 12 members:

### 1. Chair Jerome Powell (Consensus Leader)
- **Role:** Chair of the Federal Open Market Committee
- **Goal:** Ensure monetary policy aligns with long-term economic stability, balancing inflation control and labor market resilience.
- **Backstory:** Powell has led the Fed through economic crises, including COVID-19 and the Great Inflation Reversal. He balances aggressive inflation control (Volcker-style) with employment protection (Bernanke-style).
- **Decision Factors:** Beige Book insights, inflation risks, labor market trends.
- **Likely Vote:** Leans toward consensus but prefers stability; cautious on rate cuts unless inflation is clearly under control.

### 2. Vice Chair John Williams (Data-Driven Economist)
- **Role:** Vice Chair of the Federal Reserve
- **Goal:** Use quantitative models and historical Fed policy cycles to guide interest rate decisions.
- **Backstory:** Williams emphasizes empirical data, referencing global central banks and past rate cycles. He values inflation persistence metrics and soft-landing strategies (1990s).
- **Decision Factors:** CPI/PCE inflation data, global monetary trends, employment softness.
- **Likely Vote:** Supports cuts if inflation data trends downward and unemployment worsens.

### 3. Regional & Regulatory Representative (Economic Field Perspective)
- **Role:** Represents regional economies, financial stability, and banking system health.
- **Goal:** Balance the needs of labor markets, small businesses, and financial institutions.
- **Backstory:** Inspired by Bostic, Barkin, Barr, and Kugler, this representative monitors the banking sector (post-2008 reforms), regional labor markets (job trends), and global competitiveness (capital flows).
- **Decision Factors:** Unemployment spikes, credit liquidity concerns, global rate adjustments.
- **Likely Vote:** Pushes for cuts if regional economic strain or banking liquidity risks emerge.

### How These Three Capture the Original 12:
- Powell represents the stabilizers (himself, Bowman, Daly, Waller).
- Williams represents the data-driven economists (Cook, Jefferson, Kugler).
- he third agent integrates regional perspectives, financial stability concerns, and global trends (Bostic, Barkin, Barr, and others).
*This setup ensures the voting dynamics still reflect the broader FOMC’s policy inclinations.*