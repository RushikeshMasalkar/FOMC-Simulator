from crewai import Agent, Crew, Process, Task
from crewai.knowledge.storage.knowledge_storage import KnowledgeStorage
from crewai.project import CrewBase, agent, crew, task
from crewai.knowledge.source.pdf_knowledge_source import PDFKnowledgeSource
from crewai.knowledge.source.csv_knowledge_source import CSVKnowledgeSource
from crewai.memory import LongTermMemory, ShortTermMemory, EntityMemory
from crewai.memory.storage.ltm_sqlite_storage import LTMSQLiteStorage
from crewai import LLM
from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

import httpx

def initialize_knowledge_sources(meeting_code):
    """Initialize meeting-specific knowledge sources."""

    embedder_config = {
        "provider": "ollama",
        "config": {
            "model": "nomic-embed-text",
            "url": "http://localhost:11434/api/embeddings",
        },
    }

    knowledge_storage = KnowledgeStorage(
        embedder=embedder_config,
        collection_name=f"fomc_{meeting_code}_nomic",
    )

    knowledge_storage.initialize_knowledge_storage()
    knowledge_storage.embedder._session = httpx.Client(timeout=600.0)

    pdf_source = PDFKnowledgeSource(
        file_paths=[f"{meeting_code} dot plot description.pdf"],
        storage=knowledge_storage,
    )

    csv_source = CSVKnowledgeSource(
        file_paths=[f"{meeting_code} historical macro_filtered.csv"],
        chunk_size=500,
        chunk_overlap=50,
        storage=knowledge_storage,
    )

    return knowledge_storage, pdf_source, csv_source, embedder_config


# Gemini LLM configuration
managerllm = LLM(
    model="gemini/gemini-3.1-flash-lite",
    temperature=0.03,
    api_key=os.environ["GEMINI_API_KEY"],
)

gpt_llm = LLM(
    model="gemini/gemini-3.1-flash-lite",
    temperature=0.03,
    api_key=os.environ["GEMINI_API_KEY"],
)

@CrewBase
class BnyCapstoneCrew:
    """FOMC Simulation crew with initial member analysis"""

    def __init__(self, meeting_code="24_9", run_number=1):
        self.meeting_code = meeting_code
        self.run_number = run_number

        (
            self.knowledge_storage,
            self.pdf_source,
            self.csv_source,
            self.embedder_config,
        ) = initialize_knowledge_sources(meeting_code)

    agents_config = "config/agents.yaml"
    # tasks not needed as we have to manually change them
    tasks_config = "config/tasks.yaml"

    # Define agents
    @agent
    def economist(self) -> Agent:
        return Agent(
            config=self.agents_config["economist"],
            verbose=True,
            llm=managerllm,
            knowledge_sources=[self.pdf_source, self.csv_source],
            knowledge_storage=self.knowledge_storage,
            embedder=self.embedder_config,  # Use Gemini embeddings
            max_iter=50,
        )

    @agent
    def Regional_Pragmatists(self) -> Agent:
        return Agent(
            config=self.agents_config["Regional_Pragmatists"],
            verbose=True,
            llm=gpt_llm,
            knowledge_sources=[self.pdf_source, self.csv_source],
            knowledge_storage=self.knowledge_storage,
            embedder=self.embedder_config,  # Add this line
            max_iter=50,
            memory=True,
        )

    @agent
    def Academic_Balancers(self) -> Agent:
        return Agent(
            config=self.agents_config["Academic_Balancers"],
            verbose=True,
            llm=gpt_llm,
            knowledge_sources=[self.pdf_source, self.csv_source],
            knowledge_storage=self.knowledge_storage,
            embedder=self.embedder_config,
            max_iter=50,
            memory=True,
        )

    @agent
    def Central_Policymakers(self) -> Agent:
        return Agent(
            config=self.agents_config["Central_Policymakers"],
            verbose=True,
            llm=gpt_llm,
            knowledge_sources=[self.pdf_source, self.csv_source],
            knowledge_storage=self.knowledge_storage,
            embedder=self.embedder_config,
            max_iter=50,
            memory=True,
        )

    @agent
    def analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["analyst"],
            verbose=True,
            llm=gpt_llm,
            knowledge_sources=[self.pdf_source, self.csv_source],
            knowledge_storage=self.knowledge_storage,
            embedder=self.embedder_config,
            max_iter=50,
            memory=True,
        )

    # Define economist tasks
    @task
    def probabilities_comment(self) -> Task:
        return Task(
            description="""
            Make a comment about the implied probabilities in the current macro 1.pdf file.
            Explain what this means, this data comes from the CME Fedwatch website.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            """,
            agent=self.analyst(),
            expected_output="A Chain of draft style comment about the implied probabilities in the current macro 1.pdf file, explaining what it means.",
        )

    @task
    def get_economic_suggestions(self) -> Task:
        return Task(
            description="""
            As the Fed's chief economist, analyze the current economic situation using the Beige Book, 
            historical macro data, and current economic indicators provided to you. Pay attention to the 
            probabilities implied by the futures market, mentioned in the current macro 1.pdf file.
            
            Based on your analysis, propose 3 potential monetary policy solutions, each with:
            1. A title that includes a specific interest rate plan with numerical adjustment
            2. Detailed justification based on economic data
            3. Projected economic outcomes
            4. A reference to the implied probabilities of rate hikes and rate cuts mentioned
            in the knowledge material.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            
            You must not describe any preliminary leanings or advice on which option is best.
            
            Format your response clearly with each solution separated and numbered.
            Never give options that have a 0 percent implied probability. For example, if the 
            implied probabilities show a 0 percent chance of a rate hike, none of the options 
            should include increasing the rate. A 100% chance of a rate hike or a rate cut does
            not mean that the market favors a steady rate.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            """,
            agent=self.economist(),
            context=[self.probabilities_comment()],
            expected_output="A comprehensive analysis with 3-5 detailed monetary policy solutions that include the numerical Fed Funds target rate adjustment in the title of each solution.",
        )

    # Define member analysis tasks
    @task
    def regional_analysis(self) -> Task:
        return Task(
            description="""
            As Regional Pragmatists, analyze the economist's proposed monetary policy solutions.
            Also, make a prediction for what the Federal Funds Target rate will be at the end
            of the year in 2025. Pay attention to the probabilities implied by the futures market, 
            mentioned in the current macro 1.pdf file.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            
            Consider:
            1. The current economic data from the Beige Book
            2. Historical macroeconomic data
            3. Current macroeconmic data
            4. The fed statement and minutes from the prior meeting
            5. Your perspective on inflation, employment, and financial stability
            
            Provide your initial assessment of each proposed solution, highlighting strengths, 
            weaknesses, and your preliminary leanings. Focus on how these policies align with the 
            Fed's dual mandate of price stability and maximum employment. Make specific historical
            comparisons using exact dates, and reference specific macroeconomic indicators.
            
            End with your preliminary thoughts on which direction you're leaning and why.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            """,
            agent=self.Regional_Pragmatists(),
            context=[self.get_economic_suggestions(), self.probabilities_comment()],
            expected_output="Regional Pragmatists' detailed analysis of proposed solutions with initial position, specific historical comparisons to exact dates, and a prediction for the Fed Funds target rate at the end of 2025.",
        )

    @task
    def academic_analysis(self) -> Task:
        return Task(
            description="""
            As Academic Balancers, analyze the economist's proposed monetary policy solutions.
            Also, make a prediction for what the Federal Funds Target rate will be at the end
            of the year in 2025. Pay attention to the probabilities implied by the futures market, 
            mentioned in the current macro 1.pdf file.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            
            Consider:
            1. The current economic data from the Beige Book
            2. Historical macroeconomic data
            3. Current macroeconmic data
            4. The fed statement and minutes from the prior meeting
            5. Your perspective on inflation, employment, and financial stability
            
            Provide your initial assessment of each proposed solution, highlighting strengths, 
            weaknesses, and your preliminary leanings. Focus on how these policies align with the 
            Fed's dual mandate of price stability and maximum employment. Make specific historical
            comparisons using exact dates, and reference specific macroeconomic indicators.
            
            End with your preliminary thoughts on which direction you're leaning and why.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            """,
            agent=self.Academic_Balancers(),
            context=[self.get_economic_suggestions(), self.probabilities_comment()],
            expected_output="Academic Balancers' detailed analysis of proposed solutions with initial position, specific historical comparisons to exact dates, and a prediction for the Fed Funds target rate at the end of 2025.",
        )

    @task
    def central_analysis(self) -> Task:
        return Task(
            description="""
            As Central Policymakers, analyze the economist's proposed monetary policy solutions.
            Also, make a prediction for what the Federal Funds Target rate will be at the end
            of the year in 2025. Pay attention to the probabilities implied by the futures market, 
            mentioned in the current macro 1.pdf file.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            
            Consider:
            1. The current economic data from the Beige Book
            2. Historical macroeconomic data
            3. Current macroeconmic data
            4. The fed statement and minutes from the prior meeting
            5. Your perspective on inflation, employment, and financial stability
            
            Provide your initial assessment of each proposed solution, highlighting strengths, 
            weaknesses, and your preliminary leanings. Focus on how these policies align with the 
            Fed's dual mandate of price stability and maximum employment. Make specific historical
            comparisons using exact dates, and reference specific macroeconomic indicators.
            
            End with your preliminary thoughts on which direction you're leaning and why.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            """,
            agent=self.Central_Policymakers(),
            context=[self.get_economic_suggestions(), self.probabilities_comment()],
            expected_output="Central Policymakers' detailed analysis of proposed solutions with initial position, specific historical comparisons to exact dates, and a prediction for the Fed Funds target rate at the end of 2025.",
        )

    # Define individual discussion tasks for each member
    @task
    def regional_discussion(self) -> Task:
        return Task(
            description="""
            As Regional Pragmatists, respond to the initial analyses provided by your colleagues:
            
            1. Address key points raised by other members that align with or differ from your perspective
            2. Clarify or defend your position based on questions or concerns from others
            3. Note any shifts in your thinking based on others' analyses
            4. Highlight regional economic considerations that may have been overlooked
            
            Be specific in your references to other members' positions. Provide your assessment
            of where the committee appears to be leaning and the key factors that should inform the final decision.
            Be sure to continue to reference specific historical comparisons, and specific macroeconomic indicators.
            Provide an updated prediction for the fed funds target rate at the end of 2025.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            """,
            agent=self.Regional_Pragmatists(),
            context=[
                self.regional_analysis(),
                self.academic_analysis(),
                self.central_analysis(),
            ],
            expected_output="Regional Pragmatists' response to member analyses and assessment of committee direction. This includes a vote for what to do with the interest rate, specific historical comparisons to exact dates, mentions of specific metrics, and a prediction for the fed funds target rate at the end of 2025.",
        )

    @task
    def academic_discussion(self) -> Task:
        return Task(
            description="""
            As Academic Balancers, respond to the initial analyses provided by your colleagues:
            
            1. Address key points raised by other members that align with or differ from your perspective
            2. Clarify or defend your position based on questions or concerns from others
            3. Note any shifts in your thinking based on others' analyses
            4. Highlight the most important considerations that should guide the committee's decision
            
            Be specific in your references to other members' positions. Be sure to continue to reference
            specific historical comparisons, and specific macroeconomic indicators.
            Provide an updated prediction for the fed funds target rate at the end of 2025.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            """,
            agent=self.Academic_Balancers(),
            context=[
                self.regional_analysis(),
                self.academic_analysis(),
                self.central_analysis(),
            ],
            expected_output="Academic Balancers' response to member analyses. This includes a vote for what to do with the interest rate, specific historical comparisons to exact dates, mentions of specific metrics, and a prediction for the fed funds target rate at the end of 2025.",
        )

    @task
    def central_discussion(self) -> Task:
        return Task(
            description="""
            As Central Policymakers, respond to the initial analyses provided by your colleagues:
            
            1. Address key points raised by other members that align with or differ from your perspective
            2. Clarify or defend your position based on questions or concerns from others
            3. Note any shifts in your thinking based on others' analyses
            
            Be specific in your references to other members' positions. Be sure to continue to reference
            specific historical comparisons, and specific macroeconomic indicators.
            Provide an updated prediction for the fed funds target rate at the end of 2025.
            Use Chain of Draft: Analyze with minimal steps, 30 words max per step.
            """,
            agent=self.Central_Policymakers(),
            context=[
                self.regional_analysis(),
                self.academic_analysis(),
                self.central_analysis(),
            ],
            expected_output="Central Policymakers' response to member analyses. This includes a vote for what to do with the interest rate, specific historical comparisons to exact dates, mentions of specific metrics, and a prediction for the fed funds target rate at the end of 2025.",
        )

    @task
    def regional_vote(self) -> Task:
        return Task(
            description="""
            As Regional Pragmatists, cast your final vote for the September 18, 2024 FOMC meeting.

                1. Clearly state which policy option you are voting for.
                2. Provide a concise explanation for your vote (1–2 paragraphs).
                3. Include relevant historical comparisons.
                4. Reference specific macroeconomic metrics and indicators.
                5. Note any reservations or conditions to your support.

                Express your vote as the change in the Federal Funds Target rate:
                - Maintain: 0.00%
                - Increase by 25 basis points: +0.25%
                - Decrease by 25 basis points: -0.25%
                - Decrease by 50 basis points: -0.50%

                Use Chain of Draft: Analyze with minimal steps, 30 words maximum per step.

                Format your response as:

                Regional Pragmatist:
                POLICY VOTE: [Policy Option #]
                INTEREST RATE VOTE: [Rate adjustment]
                SPECIFIC HISTORICAL COMPARISONS: [Relevant comparisons]
                EXPLANATION: [Explanation with specific economic metrics]
                """,
                agent=self.Regional_Pragmatists(),
                context=[
                    self.regional_discussion(),
                    self.academic_discussion(),
                    self.central_discussion(),
                ],
                expected_output="""
                Regional Pragmatist's final policy vote, including the numerical
                interest-rate adjustment, historical comparisons, and explanation.
                """,
        )



    @task
    def academic_vote(self) -> Task:
        return Task(
            description="""
            As Academic Balancers, cast your final vote for the September 18, 2024 FOMC meeting.

                1. Clearly state which policy option you are voting for.
                2. Provide a concise explanation for your vote (1–2 paragraphs).
                3. Include relevant historical comparisons.
                4. Reference specific macroeconomic metrics and indicators.
                5. Note any reservations or conditions to your support.

                Express your vote as the change in the Federal Funds Target rate:
                - Maintain: 0.00%
                - Increase by 25 basis points: +0.25%
                - Decrease by 25 basis points: -0.25%
                - Decrease by 50 basis points: -0.50%

                Use Chain of Draft: Analyze with minimal steps, 30 words maximum per step.

                Format your response as:

                Academic Balancer:
                POLICY VOTE: [Policy Option #]
                INTEREST RATE VOTE: [Rate adjustment]
                SPECIFIC HISTORICAL COMPARISONS: [Relevant comparisons]
                EXPLANATION: [Explanation with specific economic metrics]
                """,
                agent=self.Academic_Balancers(),
                context=[
                    self.regional_discussion(),
                    self.academic_discussion(),
                    self.central_discussion(),
                ],
                expected_output="""
                Academic Balancer's final policy vote, including the numerical
                interest-rate adjustment, historical comparisons, and explanation.
                """,
        )



    @task
    def central_vote(self) -> Task:
        return Task(
            description="""
            As Central Policymakers, cast your final vote for the September 18, 2024 FOMC meeting.

                1. Clearly state which policy option you are voting for.
                2. Provide a concise explanation for your vote (1–2 paragraphs).
                3. Include relevant historical comparisons.
                4. Reference specific macroeconomic metrics and indicators.
                5. Note any reservations or conditions to your support.

                Express your vote as the change in the Federal Funds Target rate:
                - Maintain: 0.00%
                - Increase by 25 basis points: +0.25%
                - Decrease by 25 basis points: -0.25%
                - Decrease by 50 basis points: -0.50%

                Use Chain of Draft: Analyze with minimal steps, 30 words maximum per step.

                Format your response as:

                Central Policymaker:
                POLICY VOTE: [Policy Option #]
                INTEREST RATE VOTE: [Rate adjustment]
                SPECIFIC HISTORICAL COMPARISONS: [Relevant comparisons]
                EXPLANATION: [Explanation with specific economic metrics]
                """,
                agent=self.Central_Policymakers(),
                context=[
                    self.regional_discussion(),
                    self.academic_discussion(),
                    self.central_discussion(),
                ],
                expected_output="""
                Central Policymaker's final policy vote, including the numerical
                interest-rate adjustment, historical comparisons, and explanation.
                """,
        )



    @task
    def other_summary(self) -> Task:
        return Task(
            description="""
            As the FOMC analyst, draft a formal public statement on behalf of the
            Federal Open Market Committee (FOMC), with a total length of approximately
            500–700 words.

                This statement should:
                - Clearly announce the interest rate decision made by the committee (based on the final votes).
                - Summarize the economic conditions informing the decision, including inflation trends, labor market developments, GDP growth, financial stability, and risks.
                - Reference macroeconomic indicators and specific historical analogs mentioned by members.
                - Include regional variations or sectoral insights if discussed.
                - Provide forward guidance on future rate policy and the outlook for inflation and employment.
                - Be written in the same tone, structure, and formality as official FOMC post-meeting statements.

                Formatting Instructions:
                - Begin with a 1–2 paragraph overview of the decision.
                - Follow with an economic data and risk assessment.
                - End with forward guidance and policy direction.
                - Avoid headings or markdown.

                Word Count Guidance:
                - Aim for approximately 500–700 words.
                - Avoid unnecessary repetition or artificial sentence expansion.
                """,
                agent=self.analyst(),
                context=[
                    self.regional_analysis(),
                    self.academic_analysis(),
                    self.central_analysis(),
                    self.regional_discussion(),
                    self.academic_discussion(),
                    self.central_discussion(),
                    self.regional_vote(),
                    self.academic_vote(),
                    self.central_vote(),
                ],
                expected_output="""
                A fully formatted FOMC post-meeting statement, approximately 500–700 words,
                suitable for public release. It includes:
                (1) the interest rate decision,
                (2) macroeconomic and financial assessment,
                (3) forward guidance.
                Written in professional FOMC style with no AI mention.
                """,
        )



    @task
    def vote_summary(self) -> Task:
        return Task(
            description="""
           As the FOMC analyst, prepare a JSON summary of the final votes from all three members voting tasks.
           The votes should be in terms of the change in interest rate they voted for, accurately reflecting INTEREST RATE VOTE from each respective member's voting task.
           The output must strictly follow this format:
           {{
               "rate_votes": [
                   {{"member": "Regional Pragmatist", "vote": "#.##%"}}, #INTEREST RATE VOTE from regional_vote task
                   {{"member": "Academic Balancer", "vote": "#.##%"}}, #INTEREST RATE VOTE from academic_vote task
                   {{"member": "Central Policymaker", "vote": "#.##%"}} #INTEREST RATE VOTE from central_vote task
               ],
           Important Notes:
           - The summary should accurately reflect the final votes from each member's voting task.
           - The JSON must be correctly formatted with no additional text, markdown, or surrounding explanations.
           """,
            agent=self.analyst(),
            context=[
                self.regional_vote(),
                self.academic_vote(),
                self.central_vote(),
            ],
            expected_output="""
           {{
               "rate_votes": [
                   {{"member": "Regional Pragmatist", "vote": "#.##%"}}, #INTEREST RATE VOTE from regional_vote task
                   {{"member": "Academic Balancer", "vote": "#.##%"}}, #INTEREST RATE VOTE from academic_vote task
                   {{"member": "Central Policymaker", "vote": "#.##%"}} #INTEREST RATE VOTE from central_vote task
               ]
           }}
           """,
        )

    
    @task
    def summary_final(self) -> Task:
        return Task(
            description="""
            Combine the outputs of other_summary and vote_summary into one JSON object.

            The output must strictly follow this format:
            {{
                "fomc_public_statement": "Full FOMC-style qualitative statement text goes here.",
                "rate_votes": [
                    {{"member": "Regional Pragmatist", "vote": "#.##%"}},
                    {{"member": "Academic Balancer", "vote": "#.##%"}},
                    {{"member": "Central Policymaker", "vote": "#.##%"}}
                ]
            }}

            Return valid JSON without additional text or markdown.
            """,
            agent=self.analyst(),
            context=[
                self.other_summary(),
                self.vote_summary(),
            ],
            expected_output="""
            A valid JSON object containing the FOMC public statement and
            the final interest-rate votes of all three members.
            """,
            output_file=f"results/{self.meeting_code}_run{self.run_number}/rate_summary.json",
        )

    @crew
    def crew(self) -> Crew:
        """Creates the FOMC simulation crew"""
        return Crew(
            agents=[
                self.economist(),
                self.Regional_Pragmatists(),
                self.Academic_Balancers(),
                self.Central_Policymakers(),
                self.analyst(),
            ],
            tasks=[
                # First get economic suggestions
                self.probabilities_comment(),
                self.get_economic_suggestions(),
                # Then have each member analyze the suggestions
                self.regional_analysis(),
                self.academic_analysis(),
                self.central_analysis(),
                # Then have individual discussion contributions from each member
                self.regional_discussion(),
                self.academic_discussion(),
                self.central_discussion(),
                # Then voting
                self.regional_vote(),
                self.academic_vote(),
                self.central_vote(),
                # Finally summary and statement
                self.other_summary(),
                self.vote_summary(),
                self.summary_final(),
            ],
            process=Process.sequential,  # Use sequential process to ensure proper order
            verbose=True,
            memory=False,
            output_log_file=f"results/{self.meeting_code}_run{self.run_number}/fomc_simulation.md",
        )
