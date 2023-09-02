
# HackBot - AI Cybersecurity Chatbot
```text
     _   _            _    ____        _   
    | | | | __ _  ___| | _| __ )  ___ | |_ 
    | |_| |/ _` |/ __| |/ /  _ \ / _ \| __| By: Morpheuslord
    |  _  | (_| | (__|   <| |_) | (_) | |_  AI used: Meta-LLama2
    |_| |_|\__,_|\___|_|\_\____/ \___/ \__|
```

## Introduction

Welcome to HackBot, an AI-powered cybersecurity chatbot designed to provide helpful and accurate answers to your cybersecurity-related queries and also do code analysis and scan analysis. Whether you are a security researcher, an ethical hacker, or just curious about cybersecurity, HackBot is here to assist you in finding the information you need.

HackBot utilizes the powerful language model Meta-LLama2 through the "LlamaCpp" library. This allows HackBot to respond to your questions in a coherent and relevant manner. Please make sure to keep your queries in English and adhere to the guidelines provided to get the best results from HackBot.

## Features
- **Local AI/ Runpod Deployment Support:** I have added an option using which you can easily deploy the Hackbot chat interface and use llama in 2 ways:
 - *Using RunPod:* You can use runpod serverless endpoint deployment of llama and connect them to the chatbot by changing the `AI_OPTION` section of the .env file for `Runpod you need to use RUNPOD` and for `Local Llama deployment LOCALLLAMA`. `RUNPOD` & `LOCALLLAMA`
 - *Key Notes:* For the runpod version of the llama to work you need to make sure the `RUNPOD ID` and your `RUNPOD API KEY` are set. 
- **AI Cybersecurity Chat:** HackBot can answer various cybersecurity-related queries, helping you with penetration testing, security analysis, and more.
- **Interactive Interface:** The chatbot provides an interactive command-line interface, making it easy to have conversations with HackBot.
- **Clear Output:** HackBot presents its responses in a well-formatted markdown, providing easily readable and organized answers.
- **Static Code Analysis:** Utilizes the provided scan data or log file for conducting static code analysis. It thoroughly examines the source code without executing it, identifying potential vulnerabilities, coding errors, and security issues.
- **Vulnerability Analysis:** Performs a comprehensive vulnerability analysis using the provided scan data or log file. It identifies and assesses security weaknesses, misconfigurations, and potential exploits present in the target system or network.

## How it looks
### Using Llama
Using LLama2 is one of the best offline and free options out there. It is currently under improvement I am working on a prompt that will better incorporate cybersecurity perspective into the AI.
I have to thank **@thisserand** and his [llama2_local](https://github.com/thisserand/llama2_local) repo and also his YT video [YT_Video](https://youtu.be/WzCS8z9GqHw). They were great resources. To be frank the llama2 code is 95% his, I just yanked the code and added a Flask API functionality to it.

The Accuracy of the AI offline and outside the codes test was great and had equal accuracy to openai or bard but while in code it was facing a few issues may be because of the prompting and all. I will try and fix it.
The speed depends on your system and the GPU and CPU configs you have. currently, it is using the `TheBloke/Llama-2-7B-Chat-GGML` model and can be changed via the `portscanner` and `dnsrecon` files.

For now, the llama code and scans are handled differently. After a few tests, I found out llama needs to be trained a little to operate like how I intended it to work so it needs some time. Any suggestions on how I can do that can be added to the discussions of this repo [Discussions Link](https://github.com/morpheuslord/GPT_Vuln-analyzer/discussions). For now, the output won't be a divided list of all the data instead will be an explanation of the vulnerability or issues discovered by the AI.

The prompt for the model usage looks like this:
```prompt
[INST] <<SYS>> {user_instruction}<</SYS>> NMAP Data to be analyzed: {user_message} [/INST]
```
The instructions looks like this:
```prompt
    Do a NMAP scan analysis on the provided NMAP scan information. The NMAP output must return in a asked format accorging to the provided output format. The data must be accurate in regards towards a pentest report.
    The data must follow the following rules:
    1) The NMAP scans must be done from a pentester point of view
    2) The final output must be minimal according to the format given.
    3) The final output must be kept to a minimal.
    4) If a value not found in the scan just mention an empty string.
    5) Analyze everything even the smallest of data.
    6) Completely analyze the data provided and give a confirm answer using the output format.
    7) mention all the data you found in the output format provided so that regex can be used on it.
    8) avoid unnecessary explaination.
    9) the critical score must be calculated based on the CVE if present or by the nature of the services open
    10) the os information must contain the OS used my the target.
    11) the open ports must include all the open ports listed in the data[tcp] and varifying if it by checking its states value.  you should not negect even one open port.
    12) the vulnerable services can be determined via speculation of the service nature or by analyzing the CVE's found.
    The output format:
        critical score:
        - Give info on the criticality
        "os information":
        - List out the OS information
        "open ports and services":
        - List open ports
        - List open ports services
        "vulnerable service":
        - Based on CVEs or nature of the ports opened list the vulnerable services
        "found cve":
        - List the CVE's found and list the main issues.
```

Using the instruction set and the data provided via the prompt the llama AI generates its output.

For the most usage I suggest you create an runpod serverless endpoit deployment of llama you can refer this tutorial for that [tutorial](https://www.youtube.com/watch?v=Ftb4vbGUr7U). Follow the tutorial for better use.
### Chat:
![HackBot_chat](https://github.com/morpheuslord/HackBot/assets/70637311/01a95209-6037-45c6-aadc-30919abccf7e)

### Static Code analysis:
![code_analysis](https://github.com/morpheuslord/HackBot/assets/70637311/52ef1b07-4cf0-464e-91ac-9e3b7d015cb2)

### Vulnerability analysis:
![vuln_analysis](https://github.com/morpheuslord/HackBot/assets/70637311/6683b226-425e-4862-b254-f155f8f7b57d)

## Installation

### Prerequisites

Before you proceed with the installation, ensure you have the following prerequisites:

- Python (3.11 or later)
- `pip3` package manager
- `Visual studio Code` - Follow the steps in this link [llama-cpp-prereq-install-instructions](https://github.com/abetlen/llama-cpp-python)
- `cmake`

### Step 1: Clone the Repository

```bash
git clone https://github.com/morpheuslord/hackbot.git
cd hackbot
```

### Step 2: Install Dependencies

```bash
pip3 install -r requirements.txt
```

### Step 3: Download the AI Model

```bash
python3 hackbot.py
```

The first time you run HackBot, it will check for the AI model required for the chatbot. If the model is not present, it will be automatically downloaded and saved as "llama-2-7b-chat.ggmlv3.q4_0.bin" in the project directory.

## Usage

To start a conversation with HackBot, run the following command:

### For Local LLama users
The `.env` file must look like this:
```env
RUNPOD_ENDPOINT_ID = ""
RUNPOD_API_KEY = ""
AI_OPTION = "LLAMALOCAL"
```
After that is done run this.
```bash
python hackbot.py
```
### For RunPod LLama users
The `.env` file must look like this:
```env
RUNPOD_ENDPOINT_ID = "<<SERVERLESS ENDPOINT ID>>"
RUNPOD_API_KEY = "<<RUNPOD API KEY>>"
AI_OPTION = "RUNPOD"
```
After that is done run this.
```bash
python3 hackbot.py
```

HackBot will display a banner and wait for your input. You can ask cybersecurity-related questions, and HackBot will respond with informative answers. To exit the chat, simply type "quit_bot" in the input prompt.

Here are some additional commands you can use:

- `clear_screen`: Clears the console screen for better readability.
- `quit_bot`: This is used to quit the chat application
- `bot_banner`: Prints the default bots banner.
- `contact_dev`: Provides my contact information.
- `save_chat`: Saves the current session interactions.
- `vuln_analysis`: Does a Vuln analysis using the scan data or log file.
- `static_code_analysis`: Does a Static code analysis using the scan data or log file.

**Note:** I am working on more addons and more such commands to give a more chatGPT experience

**Please Note:** HackBot's responses are based on the Meta-LLama2 AI model, and its accuracy depends on the quality of the queries and data provided to it.

I am also working on AI training by which I can teach it how to be more accurately tuned to work for hackers on a much more professional level.

## Contributing

We welcome contributions to improve HackBot's functionality and accuracy. If you encounter any issues or have suggestions for enhancements, please feel free to open an issue or submit a pull request. Follow these steps to contribute:

1. Fork the repository.
2. Create a new branch with a descriptive name.
3. Make your changes and commit them.
4. Push your changes to your forked repository.
5. Open a pull request to the `main` branch of this repository.

Please maintain a clean commit history and adhere to the project's coding guidelines.

## AI training
If anyone with the know-how of training text generation models can help improve the code. For the AI training part, I have prepared a dataset and a working code for the training but I am facing issues with the training part and collaboration on that will be appreciated.
You can view the dataset on :
- [HuggingFace](https://huggingface.co/datasets/morpheuslord/cve-llm-training)
- [GitHub](https://github.com/morpheuslord/CVE-llm_dataset)

The Github version of the dataset is for the OpenAI training and the other is for Llama2-7b from meta. The AIM of the dataset is to try and possibly generate an AI model capable enough to better work with CVE data. If you feel the dataset is lacking then feel free to modify and share your views.
test
## Contact

For any questions, feedback, or inquiries related to HackBot, feel free to contact the project maintainer:

- Email: morpheuslord@protonmail.com
- Twitter: [@morpheuslord2](https://twitter.com/morpheuslord2)
- LinkedIn: [ChiranjeeviG](https://www.linkedin.com/in/chiranjeevi-g-naidu/)
