import os
import platform
import json
from subprocess import run
from langchain.llms import LlamaCpp
from langchain import PromptTemplate
from langchain.callbacks.manager import CallbackManager
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from rich.prompt import Prompt
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.console import Group
from rich.align import Align
from rich import box
from rich.markdown import Markdown
from typing import Any


def check_for_model() -> None:
    url = "https://huggingface.co/localmodels/Llama-2-7B-Chat-ggml/resolve/main/llama-2-7b-chat.ggmlv3.q4_0.bin"
    path = 'llama-2-7b-chat.ggmlv3.q4_0.bin'
    isExist = os.path.exists(path)
    if isExist == True:
        pass
    elif isExist == False:
        run(f"wget {url}", shell=True)
        pass

check_for_model()
template = """persona: {persona}
You are a helpful, respectful and honest cybersecurity analyst.
Being a security analyst you must scrutanize the details provided to ensure
it is usable for penitration testing. Please ensure that your responses are
socially unbiased and positive in nature. If a question does not make any
sense, or is not factually coherent, explain why instead of answering
something not correct. If you don't know the answer to a question,
please don't share false information.
keep your answers in english and do not divert from the question.
If the answer to the asked question or query is complete end your answering
keep the answering accurate and do not skip details related to the query.
Give your output in markdown format.
"""

console = Console()
prompt = PromptTemplate(template=template, input_variables=["persona"])
chat_history = []
callback_manager = CallbackManager([StreamingStdOutCallbackHandler()])

llm = LlamaCpp(
    model_path="llama-2-7b-chat.ggmlv3.q4_0.bin",
    input={"temperature": 0.75, "max_length": 2000, "top_p": 1},
    callback_manager=callback_manager,
    max_tokens=2000,
    n_batch=1000,
    n_gpu_layers=50,
    verbose=False,
    n_ctx=3500,
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
                pass
            else:
                prompt = prompt_in
                print(Print_AI_out(prompt))
                pass
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()