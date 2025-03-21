"""Chat functionality for YouTube video analysis."""

import os
import time
from typing import Dict, Any, Optional, List

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import Tool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, BaseMessage
from langgraph.prebuilt import create_react_agent
from langchain.schema import Document
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_tavily import TavilySearch
import streamlit as st
import os

from dotenv import load_dotenv

load_dotenv()

from .utils.logging import get_logger
from .utils.youtube_utils import extract_video_id, get_video_info

# Configure logging
logger = get_logger("chat")

def create_vectorstore(text: str, transcript_list: Optional[List[Dict[str, Any]]] = None) -> FAISS:
    """
    Create a vector store from the text.
    
    Args:
        text: The text to create the vector store from
        transcript_list: Optional list of transcript items with timestamps
        
    Returns:
        A FAISS vector store
    """
    # Split the text into chunks
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
    )
    
    if transcript_list:
        # Process transcript with timestamps
        chunks = []
        metadata_list = []
        
        # Process transcript in chunks
        current_chunk = ""
        current_chunk_metadata = {
            "start_time": None,
            "end_time": None
        }
        
        for item in transcript_list:
            # Convert seconds to MM:SS format
            seconds = int(item['start'])
            minutes, seconds = divmod(seconds, 60)
            timestamp = f"{minutes:02d}:{seconds:02d}"
            
            # Add text with timestamp
            text_with_timestamp = f"[{timestamp}] {item['text']}"
            
            # If this is the first item in the chunk, set the start time
            if current_chunk_metadata["start_time"] is None:
                current_chunk_metadata["start_time"] = timestamp
                current_chunk_metadata["start_seconds"] = item['start']
            
            # Update the end time for each item
            current_chunk_metadata["end_time"] = timestamp
            current_chunk_metadata["end_seconds"] = item['start'] + item.get('duration', 0)
            
            # Add to current chunk
            if current_chunk:
                current_chunk += " "
            current_chunk += text_with_timestamp
            
            # If chunk is large enough, add it to chunks and reset
            if len(current_chunk) >= 1000:
                chunks.append(current_chunk)
                metadata_list.append(current_chunk_metadata.copy())
                current_chunk = ""
                current_chunk_metadata = {
                    "start_time": None,
                    "end_time": None
                }
        
        # Add the last chunk if it's not empty
        if current_chunk:
            chunks.append(current_chunk)
            metadata_list.append(current_chunk_metadata)
        
        # Create embeddings
        embeddings = OpenAIEmbeddings()
        
        # Create a FAISS vector store with metadata
        texts_with_metadata = [
            {"content": chunk, "metadata": metadata}
            for chunk, metadata in zip(chunks, metadata_list)
        ]
        
        vectorstore = FAISS.from_documents(
            documents=[
                Document(page_content=item["content"], metadata=item["metadata"])
                for item in texts_with_metadata
            ],
            embedding=embeddings
        )
        
        logger.info(f"Created FAISS vector store with {len(chunks)} chunks (with timestamps)")
    else:
        # Process regular transcript without timestamps
        chunks = text_splitter.split_text(text)
        
        # Create embeddings
        embeddings = OpenAIEmbeddings()
        
        # Create a FAISS vector store (in-memory)
        vectorstore = FAISS.from_texts(
            texts=chunks,
            embedding=embeddings
        )
        
        logger.info(f"Created FAISS vector store with {len(chunks)} chunks")
    
    return vectorstore

def create_agent_graph(vectorstore: FAISS, video_metadata: Dict[str, Any], has_timestamps: bool = False):
    """
    Create a LangGraph agent with tools for handling YouTube content queries.
    
    Args:
        vectorstore: The vector store to use for retrieval
        video_metadata: Metadata about the YouTube video
        has_timestamps: Whether the transcript has timestamps
        
    Returns:
        A LangGraph agent
    """
    # Create language model
    # Get model settings from streamlit session state if available, otherwise use env vars
    if "settings" in st.session_state and st.session_state.settings:
        model_name = st.session_state.settings.get("model", os.environ.get("LLM_MODEL", "gpt-4o-mini"))
        temperature = float(st.session_state.settings.get("temperature", os.environ.get("LLM_TEMPERATURE", "0.2")))
    else:
        model_name = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        temperature = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
    logger.info(f"Creating chat agent with model: {model_name}, temperature: {temperature}")
    if model_name.startswith("gpt"):
        llm = ChatOpenAI(temperature=temperature, model=model_name)
    elif model_name.startswith("claude"):
        llm = ChatAnthropic(temperature=temperature, model=model_name, api_key=os.getenv("ANTHROPIC_API_KEY"))
    elif model_name.startswith("gemini"):
        # Format the model name to include the 'gemini/' prefix for LiteLLM
        llm = ChatGoogleGenerativeAI(temperature=temperature, model=model_name, api_key=os.getenv("GEMINI_API_KEY"))

    # Create Tavily search tool
    search_tool = TavilySearch(
        max_results=5
    )
    
    # Create retriever tool
    retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
    
    # Define a wrapper function for the retriever to log the results
    def search_with_logging(query):
        logger.info(f"Searching for: {query}")
        results = retriever.invoke(query)
        
        # Log the retrieved chunks
        logger.info(f"Retrieved {len(results)} chunks:")
        for i, doc in enumerate(results):
            content = doc.page_content[:100] + "..." if len(doc.page_content) > 100 else doc.page_content
            metadata_str = str(doc.metadata)
            logger.info(f"Chunk {i+1}: {content} | Metadata: {metadata_str}")
        
        if has_timestamps:
            formatted_results = format_timestamped_results(results)
        else:
            formatted_results = "\n\n".join([doc.page_content for doc in results])
        
        logger.info(f"Formatted results: {formatted_results[:200]}...")
        return formatted_results
    
    # Define the search tool with updated retriever usage
    if has_timestamps:
        yt_retriever_tool = Tool(
            name="YouTube_Video_Search",
            description=f"Useful for searching information in the YouTube video transcript. Use this tool when the user asks about the content of the video with ID {video_metadata['video_id']}.",
            func=search_with_logging
        )
    else:
        yt_retriever_tool = Tool(
            name="YouTube_Video_Search",
            description=f"Useful for searching information in the YouTube video transcript. Use this tool when the user asks about the content of the video with ID {video_metadata['video_id']}.",
            func=search_with_logging
        )
    
    # Define the video info tool with logging
    def get_video_info_with_logging(_):
        info = f"Video URL: {video_metadata['youtube_url']}\nVideo ID: {video_metadata['video_id']}\nTitle: {video_metadata.get('title', 'Unknown')}\nDescription: {video_metadata.get('description', 'No description available')}"
        logger.info(f"Video info requested: {info}")
        return info
    
    video_info_tool = Tool(
        name="YouTube_Video_Info",
        description="Provides basic information about the YouTube video being analyzed.",
        func=get_video_info_with_logging
    )
    
    # Create tools list
    tools = [yt_retriever_tool, video_info_tool, search_tool]
    
    # Create system message with video description
    video_title = video_metadata.get('title', 'Unknown')
    video_description = video_metadata.get('description', 'No description available')
    
    system_message_content = f"""You are an AI assistant that helps users understand YouTube video content.
You have access to the transcript of a YouTube video titled "{video_title}" with the following description:

DESCRIPTION:
{video_description}

You can answer questions about this video and also handle general questions not related to the video.

For questions about the video content, use the YouTube_Video_Search tool to find relevant information in the transcript.
For questions about the video itself (URL, ID, title, description), use the YouTube_Video_Info tool.
If the user asks about a concept that is not clearly explained in the video, use the Tavily_Search tool to find relevant information online.
Also, if the question is not related to the video but if it is a valid question that can be answered by the internet, use the Tavily_Search tool to find relevant information online.
For general questions not related to the video, use your own knowledge to answer or use the Tavily_Search tool to find relevant information online.

Always be helpful, accurate, and concise in your responses.
If you don't know the answer to a question about the video, say so rather than making up information.
If you are searching the internet for information, then use clever search queries to get the most relevant information.

IMPORTANT: When answering questions about the video content, always include the timestamp citations from the transcript in your response. 
These timestamps indicate when in the video the information was mentioned. Format citations like [MM:SS] in your answers.

IMPORTANT: Use the chat history to maintain context of the conversation. Refer back to previous questions and answers when relevant.
"""
    
    logger.info(f"System message: {system_message_content[:200]}...")
    
    # Create a proper prompt template with the system message
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_message_content),
        ("placeholder", "{messages}"),
    ])
    
    # Create a wrapper for the LLM to log inputs and outputs
    class LoggingLLM:
        def __init__(self, llm):
            self.llm = llm
        
        def invoke(self, messages, **kwargs):
            logger.info(f"LLM Input: {str(messages)[:500]}...")
            response = self.llm.invoke(messages, **kwargs)
            logger.info(f"LLM Output: {str(response)[:500]}...")
            return response
        
        def __getattr__(self, name):
            return getattr(self.llm, name)
    
    logging_llm = LoggingLLM(llm)
    
    # Create the agent using LangGraph's create_react_agent with the correct signature
    try:
        # First attempt: Try with llm and tools as positional, prompt as keyword
        agent_executor = create_react_agent(logging_llm, tools, prompt=prompt)
        logger.info("Created agent with prompt as keyword argument")
    except TypeError as e:
        try:
            # Second attempt: Try with just llm and tools
            agent_executor = create_react_agent(logging_llm, tools)
            logger.info("Created agent without prompt template")
        except Exception as e:
            # If that fails too, log the error and try a different approach
            logger.error(f"Error creating agent with just llm and tools: {str(e)}")
            # Final attempt: Try with a dictionary of all parameters
            agent_executor = create_react_agent(
                llm=logging_llm, 
                tools=tools,
            )
            logger.info("Created agent with named parameters")
    except Exception as e:
        logger.error(f"Unexpected error creating agent: {str(e)}")
        raise
    
    # Return the agent executor
    return agent_executor

def format_timestamped_results(docs):
    """
    Format search results to include timestamps.
    
    Args:
        docs: The search results from the retriever
        
    Returns:
        Formatted search results with timestamps
    """
    results = []
    
    for doc in docs:
        content = doc.page_content
        metadata = doc.metadata
        
        # Add timestamp information if available
        if metadata and "start_time" in metadata and "end_time" in metadata:
            time_range = f"[{metadata['start_time']} to {metadata['end_time']}]"
            results.append(f"{time_range}\n{content}")
        else:
            results.append(content)
    
    return "\n\n".join(results)

def setup_chat_for_video(youtube_url: str, transcript: str, transcript_list: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Set up the chat functionality for a YouTube video.
    
    Args:
        youtube_url: The URL of the YouTube video
        transcript: The transcript of the video (already retrieved during analysis)
        transcript_list: Optional list of transcript items with timestamps (already retrieved)
        
    Returns:
        A dictionary containing the chat setup details
    """
    try:
        # Extract video ID
        video_id = extract_video_id(youtube_url)
        if not video_id:
            logger.error(f"Failed to extract video ID from URL: {youtube_url}")
            return None
        
        # Log that we're using the already retrieved transcript
        logger.info(f"Setting up chat using already retrieved transcript for video ID: {video_id}")
        logger.info(f"Transcript length: {len(transcript)} characters")
        if transcript_list:
            logger.info(f"Transcript list contains {len(transcript_list)} segments")
        
        # Get video information from the already retrieved data
        try:
            # Use the video_id to get video info
            video_info = get_video_info(youtube_url)
            
            if not video_info:
                logger.warning(f"Could not get video info for {video_id}, using default values")
                video_info = {
                    'video_id': video_id,
                    'title': f"YouTube Video ({video_id})",
                    'description': "Video description unavailable.",
                    'url': youtube_url
                }
            
            logger.info(f"Using video info for chat: {video_info['title']}")
        except Exception as e:
            logger.error(f"Error retrieving video info for chat: {str(e)}")
            # Provide default video info if retrieval fails
            video_info = {
                'video_id': video_id,
                'title': f"YouTube Video ({video_id})",
                'description': "Video description unavailable.",
                'youtube_url': youtube_url
            }
            logger.info(f"Using default video info for chat with {video_id}")
        
        # Check if we have timestamped transcript
        has_timestamps = transcript_list is not None and len(transcript_list) > 0
        
        # Create vector store from the provided transcript
        logger.info(f"Creating vector store from the provided transcript (has timestamps: {has_timestamps})")
        vectorstore = create_vectorstore(transcript, transcript_list)
        logger.info(f"Successfully created vector store for chat (with timestamps: {has_timestamps})")
        
        # Create video metadata
        video_metadata = {
            "video_id": video_id,
            "youtube_url": youtube_url,
            "title": video_info.get("title", f"YouTube Video ({video_id})"),
            "description": video_info.get("description", "No description available")
        }
        
        # Create agent using the same model as specified in environment variables
        # This ensures the chat model is the same as the analysis model
        logger.info("Creating chat agent with the same model settings as analysis")
        agent = create_agent_graph(vectorstore, video_metadata, has_timestamps)
        logger.info("Successfully created agent for chat")
        
        # Create thread ID for persistence
        thread_id = f"thread_{video_id}_{int(time.time())}"
        
        # Return chat setup details
        return {
            "video_id": video_id,
            "youtube_url": youtube_url,
            "title": video_info.get("title", f"YouTube Video ({video_id})"),
            "description": video_info.get("description", "No description available"),
            "agent": agent,
            "thread_id": thread_id,
            "has_timestamps": has_timestamps
        }
    except Exception as e:
        logger.error(f"Error setting up chat: {str(e)}")
        return None 