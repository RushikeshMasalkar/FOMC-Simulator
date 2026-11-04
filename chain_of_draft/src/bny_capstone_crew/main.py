#!/usr/bin/env python
import sys
import warnings

from datetime import datetime

from bny_capstone_crew.crew import BnyCapstoneCrew

from pathlib import Path

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# This main file is intended to be a way for you to run your
# crew locally, so refrain from adding unnecessary logic into this file.
# Replace with inputs you want to test with, it will automatically
# interpolate any tasks and agents information


def run():
    """
    Run the remaining 7 FOMC experiments.
    """


    meetings = [
        {"meeting_code": "23_2", "meeting_date": "February 1, 2023"},
        {"meeting_code": "23_5", "meeting_date": "May 3, 2023"},
        {"meeting_code": "24_3", "meeting_date": "March 20, 2024"},
        {"meeting_code": "24_9", "meeting_date": "September 18, 2024"},
    ]

    for meeting in meetings:
        for run_number in range(1, 3):

            meeting_code = meeting["meeting_code"]

            # Skip the experiment already completed
            if meeting_code == "23_2" and run_number == 1:
                continue

            result_dir = Path("results") / f"{meeting_code}_run{run_number}"
            result_dir.mkdir(parents=True, exist_ok=True)

            inputs = {
                "meeting_date": meeting["meeting_date"],
                "meeting_code": meeting_code,
                "run_number": run_number,
            }

            print(f"\nStarting {meeting_code} - Run {run_number}")

            try:
                BnyCapstoneCrew(
                    meeting_code=meeting_code,
                    run_number=run_number
                ).crew().kickoff(inputs=inputs)

                print(f"Completed {meeting_code} - Run {run_number}")

            except Exception as e:
                print(f"Error in {meeting_code} - Run {run_number}: {e}")
                continue






def train():
    """
    Train the crew for a given number of iterations.
    """
    inputs = {"topic": "AI LLMs"}
    try:
        BnyCapstoneCrew().crew().train(
            n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=inputs
        )

    except Exception as e:
        raise Exception(f"An error occurred while training the crew: {e}")


def replay():
    """
    Replay the crew execution from a specific task.
    """
    try:
        BnyCapstoneCrew().crew().replay(task_id=sys.argv[1])

    except Exception as e:
        raise Exception(f"An error occurred while replaying the crew: {e}")


def test():
    """
    Test the crew execution and returns the results.
    """
    inputs = {"topic": "AI LLMs"}
    try:
        BnyCapstoneCrew().crew().test(
            n_iterations=int(sys.argv[1]), openai_model_name=sys.argv[2], inputs=inputs
        )

    except Exception as e:
        raise Exception(f"An error occurred while testing the crew: {e}")
