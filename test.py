from langchain.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.vectorstores import FAISS
from sentence_transformers import SentenceTransformer
from langchain.llms import LlamaCpp
from langchain import PromptTemplate
from huggingface_hub import hf_hub_download
from langchain.callbacks.manager import CallbackManager
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain.chains.question_answering import load_qa_chain

callback_manager = CallbackManager([StreamingStdOutCallbackHandler()])

model_name_or_path = "localmodels/Llama-2-7B-Chat-ggml"
model_basename = "llama-2-7b-chat.ggmlv3.q4_0.bin"

loader = PyPDFLoader("test.pdf")
data = loader.load()

text_splitter=RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=0)
docs=text_splitter.split_documents(data)
embeddings=HuggingFaceEmbeddings(model_name="sentence-transformers/all-mpnet-base-v2")

vectorstore = FAISS.from_texts(texts=docs, embedding=embeddings)
query="YOLOv7 outperforms which models"
data_store=vectorstore.similarity_search(query)

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

chain=load_qa_chain(llm, chain_type="stuff")
chain.run(input_documents=data_store, question=query)