import os
import platform
import json
from subprocess import run
from langchain.llms import LlamaCpp
from langchain import PromptTemplate
from langchain.callbacks.manager import CallbackManager
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from huggingface_hub import hf_hub_download
from rich.prompt import Prompt
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.console import Group
from rich.align import Align
from rich import box
from rich.markdown import Markdown
from typing import Any

model_name_or_path = "localmodels/Llama-2-7B-Chat-ggml"
model_basename = "llama-2-7b-chat.ggmlv3.q4_0.bin"

model_path = hf_hub_download(repo_id=model_name_or_path, filename=model_basename)

template = """
persona: {persona}

You are a helpful, respectful, and honest cybersecurity analyst.

Being a security analyst, you must scrutinize the details provided to ensure they are usable for penetration testing. Please ensure that your responses are socially unbiased and positive in nature. If a question does not make any sense or is not factually coherent, explain why instead of answering something incorrect. If you don't know the answer to a question, please don't share false information.

Keep your answers in English and do not divert from the question. If the answer to the asked question or query is complete, end your answering. Keep the answering accurate and do not skip details related to the query.

Give your output in markdown format.
"""

console = Console()
prompt = PromptTemplate(template=template, input_variables=["persona"])
chat_history = []
callback_manager = CallbackManager([StreamingStdOutCallbackHandler()])

llm = LlamaCpp(
    model_path=model_path,
    input={"temperature": 0.75, "max_length": 4000, "top_p": 1},
    callback_manager=callback_manager,
    max_tokens=4000,
    n_batch=1000,
    n_gpu_layers=50,
    verbose=False,
    n_ctx=4000,
    streaming=False,
)


def clearscr() -> None:
    try:
        osp = platform.system()
        match osp:
            case 'Darwin':
                os.system("clear")
            case 'Linux':
                os.system("clear")
            case 'Windows':
                os.system("cls")
    except Exception:
        pass


def Print_AI_out(prompt) -> Panel:
    global chat_history
    out = llm(prompt)
    ai_out = Markdown(out)
    message_panel = Panel(
            Align.center(
                Group("\n", Align.center(ai_out)),
                vertical="middle",
            ),
            box=box.ROUNDED,
            padding=(1, 2),
            title="[b red]The HackBot AI output",
            border_style="blue",
        )
    save_data = {
        "Query": str(prompt),
        "AI Answer": str(out)
    }
    chat_history.append(save_data)
    return message_panel


def save_chat(chat_history: list[Any, Any]) -> None:
    f = open('chat_history.json', 'w+')
    f.write(json.dumps(chat_history))
    f.close


def vuln_analysis(scan_type, file_path) -> Panel:
    global chat_history
    f = open(file_path, "r")
    file_data = f.read()
    f.close
    prompt = f"""
        **Objective:**
        You are a Universal Vulnerability Analyzer powered by the Llama2 model. Your main objective is to analyze any provided scan data or log data to identify potential vulnerabilities in the target system or network. You can use the scan type or the scanner type to prepare better report.

        **Instructions:**

        1. **Data Analysis**: Thoroughly analyze the given scan data or log data to uncover vulnerabilities and security issues in the target environment.
        2. **Format Flexibility**: Be adaptable to handle various data formats, such as NMAP scans, vulnerability assessment reports, security logs, or any other relevant data.
        3. **Vulnerability Identification**: Identify different types of vulnerabilities, including but not limited to software vulnerabilities, misconfigurations, exposed sensitive information, potential security risks, and more.
        4. **Accuracy and Precision**: Ensure the analysis results are accurate and precise to provide reliable information for further actions.
        5. **Comprehensive Report**: Generate a detailed vulnerability report that includes the following sections:
            - **Vulnerability Summary**: A brief overview of the detected vulnerabilities.
            - **Software Vulnerabilities**: List of identified software vulnerabilities with their respective severity levels.
            - **Misconfigurations**: Highlight any misconfigurations found during the analysis.
            - **Exposed Sensitive Information**: Identify any exposed sensitive data, such as passwords, API keys, or usernames.
            - **Security Risks**: Flag potential security risks and their implications.
            - **Recommendations**: Provide actionable recommendations to mitigate the detected vulnerabilities.
        6. **Threat Severity**: Prioritize vulnerabilities based on their severity level to help users focus on critical issues first.
        7. **Context Awareness**: Consider the context of the target system or network when analyzing vulnerabilities. Take into account factors like system architecture, user permissions, and network topology.
        8. **Handling Unsupported Data**: If the provided data format is unsupported or unclear, politely ask for clarifications or indicate the limitations.
        9. **Language and Style**: Use clear and concise language to present the analysis results. Avoid jargon and unnecessary technicalities.

        **Input Data:**
        You will receive the scan file data or log file data in the required format as input. Ensure the data is correctly parsed and interpreted for analysis.
        
        **Output Format:**
        The vulnerability analysis report should be organized as mentioned in the "Comprehensive Report" section.
        Please perform the vulnerability analysis efficiently, considering the security implications and accuracy, and generate a detailed report that helps users understand the potential risks and take appropriate actions.

        ---
        Provide the scan type: {scan_type} 
        Provide the scan data or log data that needs to be analyzed: {file_data}
    """
    out = llm(prompt)
    ai_out = Markdown(out)
    message_panel = Panel(
            Align.center(
                Group("\n", Align.center(ai_out)),
                vertical="middle",
            ),
            box=box.ROUNDED,
            padding=(1, 2),
            title="[b red]The HackBot AI output",
            border_style="blue",
        )
    save_data = {
        "Query": str(prompt),
        "AI Answer": str(out)
    }
    chat_history.append(save_data)
    return message_panel

def static_analysis(language_used, file_path) -> Panel:
    global chat_history
    f = open(file_path, "r")
    file_data = f.read()
    f.close
    prompt = f"""
        **Objective:**
        Analyze the given programming file details to identify and clearly report bugs, vulnerabilities, and syntax errors.
        Additionally, search for potential exposure of sensitive information such as API keys, passwords, and usernames.

        **File Details:**
        - Programming Language: {language_used}
        - File Name: {file_path}
        - File Data: {file_data}
    """
    out = llm(prompt)
    ai_out = Markdown(out)
    message_panel = Panel(
            Align.center(
                Group("\n", Align.center(ai_out)),
                vertical="middle",
            ),
            box=box.ROUNDED,
            padding=(1, 2),
            title="[b red]The HackBot AI output",
            border_style="blue",
        )
    save_data = {
        "Query": str(prompt),
        "AI Answer": str(out)
    }
    chat_history.append(save_data)
    return message_panel


def main() -> None:
    clearscr()
    banner = """
     _   _            _    ____        _   
    | | | | __ _  ___| | _| __ )  ___ | |_ 
    | |_| |/ _` |/ __| |/ /  _ \ / _ \| __| By: Morpheuslord
    |  _  | (_| | (__|   <| |_) | (_) | |_  AI used: Meta-LLama2
    |_| |_|\__,_|\___|_|\_\____/ \___/ \__|
    """
    contact_dev = """
    Email = morpheuslord@protonmail.com
    Twitter = https://twitter.com/morpheuslord2
    LinkedIn https://www.linkedin.com/in/chiranjeevi-g-naidu/
    Github = https://github.com/morpheuslord
    """

    help_menu = """
    - clear_screen: Clears the console screen for better readability.
    - quit_bot: This is used to quit the chat application
    - bot_banner: Prints the default bots banner.
    - contact_dev: Provides my contact information.
    - save_chat: Saves the current sessions interactions.
    - help_menu: Lists chatbot commands.
    - vuln_analysis: Does a Vuln analysis using the scan data or log file.
    - static_code_analysis: Does a Static code analysis using the scan data or log file.
    """
    console.print(Panel(Markdown(banner)), style="bold green")
    while True:
        try:
            prompt_in = Prompt.ask('> ')
            if prompt_in == 'quit_bot':
                quit()
            elif prompt_in == 'clear_screen':
                clearscr()
                pass
            elif prompt_in == 'bot_banner':
                console.print(Panel(Markdown(banner)), style="bold green")
                pass
            elif prompt_in == 'save_chat':
                save_chat(chat_history)
                pass
            elif prompt_in == 'static_code_analysis':
                print(Markdown('----------'))
                language_used = Prompt.ask('Language Used> ')
                file_path = Prompt.ask('File Path> ')
                print(Markdown('----------'))
                print(static_analysis(language_used, file_path))
                pass
            elif prompt_in == 'vuln_analysis':
                print(Markdown('----------'))
                language_used = Prompt.ask('Scan Type > ')
                file_path = Prompt.ask('File Path > ')
                print(Markdown('----------'))
                print(static_analysis(language_used, file_path))
                pass
            elif prompt_in == 'contact_dev':
                console.print(Panel(
                        Align.center(
                            Group(Align.center(Markdown(contact_dev))),
                            vertical="middle",
                        ),
                        title= "Dev Contact",
                        border_style="red"
                    ),
                    style="bold green"
                )
                pass
            elif prompt_in == 'help_menu':
                console.print(Panel(
                        Align.center(
                            Group(Align.center(Markdown(help_menu))),
                            vertical="middle",
                        ),
                        title= "Help Menu",
                        border_style="red"
                    ),
                    style="bold green"
                )
                pass
            else:
                prompt = prompt_in
                print(Print_AI_out(prompt))
                pass
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()