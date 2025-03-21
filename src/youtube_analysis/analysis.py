"""Analysis functionality for YouTube videos."""

import re
import copy
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from .utils.logging import get_logger
from .utils.youtube_utils import extract_video_id, get_transcript, get_video_info
from .utils.cache_utils import get_cached_analysis, cache_analysis
from .crew import YouTubeAnalysisCrew
from .transcript import get_transcript_with_timestamps
from .chat import setup_chat_for_video

# Configure logging
logger = get_logger("analysis")

def extract_category(output: str) -> str:
    """
    Extract the category from the classification output.
    
    Args:
        output: The classification output
        
    Returns:
        The extracted category
    """
    # Look for category names in the output
    categories = [
        "Technology", "Business", "Education", "Health & Wellness", 
        "Science", "Finance", "Personal Development", "Entertainment"
    ]
    
    for category in categories:
        if category in output:
            return category
    
    # If no category is found, check for "Other"
    if "Other" in output:
        # Try to extract the specified category
        match = re.search(r"Other \(([^)]+)\)", output)
        if match:
            return f"Other: {match.group(1)}"
        return "Other"
    
    return "Uncategorized"

def extract_context_tag(output: str) -> str:
    """
    Extract the context tag from the classification output.
    
    Args:
        output: The classification output
        
    Returns:
        The extracted context tag
    """
    # Look for context tag names in the output
    context_tags = [
        "Tutorial", "News", "Review", "Case Study", 
        "Interview", "Opinion Piece", "How-To Guide"
    ]
    
    for tag in context_tags:
        if tag in output:
            return tag
    
    return "General"

def run_analysis(youtube_url: str, progress_callback=None, status_callback=None, use_cache: bool = True) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Run the YouTube Analysis Crew with the provided YouTube URL.
    
    Args:
        youtube_url: The URL of the YouTube video to analyze
        progress_callback: Optional callback function to update progress (0-100)
        status_callback: Optional callback function to update status messages
        use_cache: Whether to use cached analysis results if available
        
    Returns:
        A tuple containing the analysis results and any error message
    """
    try:
        # Extract video ID for thumbnail
        video_id = extract_video_id(youtube_url)
        logger.info(f"Extracted video ID: {video_id} from URL: {youtube_url}")
        logger.debug(f"use_cache setting: {use_cache}")
        
        # Check if we have cached analysis results
        cached_results = None
        if use_cache:
            logger.info(f"Checking for cached analysis for video ID: {video_id}")
            cached_results = get_cached_analysis(video_id, force_bypass=False)
        else:
            # If use_cache is False, force bypass the cache completely
            logger.info(f"Cache usage disabled, forcing new analysis for video {video_id}")
            cached_results = None  # Explicitly set to None to ensure we do a new analysis
        
        if cached_results:
            logger.info(f"Using cached analysis for video {video_id}")
            logger.debug(f"Cached results keys: {cached_results.keys()}")
            
            # Check if we have placeholder task outputs and force a new analysis if so
            if "task_outputs" in cached_results and cached_results["task_outputs"]:
                has_placeholders = False
                for key, value in cached_results["task_outputs"].items():
                    if isinstance(value, str) and "placeholder" in value.lower():
                        logger.warning(f"Found placeholder content in cached task output '{key}', forcing new analysis")
                        has_placeholders = True
                        break
                
                if has_placeholders:
                    logger.info(f"Cached analysis contains placeholder values, forcing new analysis for video {video_id}")
                    cached_results = None
            
            if cached_results:  # Only proceed with cached results if they're still valid
                if status_callback:
                    status_callback("Using cached analysis results...")
                
                if progress_callback:
                    progress_callback(100)
            
            # We need to recreate the chat agent since it's not serializable
            try:
                # Get the transcript
                transcript = get_transcript(youtube_url)
                
                # Get transcript with timestamps
                try:
                    timestamped_transcript, transcript_list = get_transcript_with_timestamps(youtube_url)
                except Exception as e:
                    logger.warning(f"Could not get transcript with timestamps: {str(e)}")
                    timestamped_transcript = None
                    transcript_list = None
                
                # Set up chat functionality
                chat_details = setup_chat_for_video(youtube_url, transcript, transcript_list)
                
                # Update the cached results with the new chat details
                cached_results["chat_details"] = chat_details
                
            except Exception as e:
                logger.warning(f"Error recreating chat agent for cached analysis: {str(e)}")
            
            return cached_results, None
        
        # Log that we're proceeding with a new analysis
        if cached_results is None:
            if use_cache:
                logger.info(f"No cached analysis found for video {video_id}, proceeding with new analysis")
            else:
                logger.info(f"Cache usage disabled, proceeding with new analysis for video {video_id}")
        
        # Update progress
        if progress_callback:
            progress_callback(0)
        if status_callback:
            status_callback("Fetching video transcript...")
        
        # Get the transcript
        transcript = get_transcript(youtube_url)
        
        if progress_callback:
            progress_callback(15)
        
        # Get transcript with timestamps
        try:
            timestamped_transcript, transcript_list = get_transcript_with_timestamps(youtube_url)
        except Exception as e:
            logger.warning(f"Could not get transcript with timestamps: {str(e)}")
            timestamped_transcript = None
            transcript_list = None
        
        if progress_callback:
            progress_callback(20)
        if status_callback:
            status_callback("Creating analysis crew...")
        
        # Set up chat functionality
        chat_details = setup_chat_for_video(youtube_url, transcript, transcript_list)
        
        if progress_callback:
            progress_callback(30)
        if status_callback:
            status_callback("Chat functionality enabled!")
        
        # Create and run the crew
        import os
        
        # Get model and temperature from environment variables
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        temperature = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
        
        logger.info(f"Creating YouTubeAnalysisCrew with model={model}, temperature={temperature}")
        crew_instance = YouTubeAnalysisCrew(model_name=model, temperature=temperature)
        
        try:
            logger.info("Setting up crew instance")
            crew = crew_instance.crew()
            logger.info(f"Successfully set up crew with {len(crew.tasks)} tasks")
            
            # Check if we have the expected tasks
            task_names = [task.name if hasattr(task, 'name') else str(task) for task in crew.tasks]
            logger.info(f"Tasks in crew: {task_names}")
            
            # Update progress
            if progress_callback:
                progress_callback(40)
            if status_callback:
                status_callback("Analyzing video content...")
            
            # Start the crew execution
            inputs = {"youtube_url": youtube_url, "transcript": transcript}
            logger.info(f"Starting crew execution with inputs: youtube_url={youtube_url}, transcript length={len(transcript) if transcript else 0}")
            
            # Execute with additional error handling
            try:
                crew_output = crew.kickoff(inputs=inputs)
                logger.info(f"Crew execution completed, output type: {type(crew_output)}")
            except Exception as crew_error:
                logger.error(f"Error during crew execution: {str(crew_error)}", exc_info=True)
                raise RuntimeError(f"CrewAI execution failed: {str(crew_error)}")
            
            # Extract task outputs
            task_outputs = {}
            logger.info(f"Number of tasks in crew: {len(crew.tasks)}")
            
            # Enhanced task output extraction
            for task in crew.tasks:
                task_name = task.name if hasattr(task, 'name') else task.__class__.__name__
                logger.info(f"Processing task: {task_name}, has output: {hasattr(task, 'output')}")
                
                if hasattr(task, 'output'):
                    if task.output:
                        task_outputs[task_name] = task.output.raw
                        output_length = len(str(task.output.raw))
                        logger.info(f"Added output for task {task_name}, length: {output_length}")
                        # Log a preview of the output for debugging
                        preview = str(task.output.raw)[:100] + "..." if len(str(task.output.raw)) > 100 else str(task.output.raw)
                        logger.info(f"Output preview for {task_name}: {preview}")
                        
                        # Update progress based on task completion
                        if task_name == "classify_video":
                            if progress_callback:
                                progress_callback(50)
                            if status_callback:
                                status_callback("Summarizing video content...")
                        elif task_name == "summarize_content":
                            if progress_callback:
                                progress_callback(70)
                            if status_callback:
                                status_callback("Analyzing video content...")
                        elif task_name == "analyze_content":
                            if progress_callback:
                                progress_callback(85)
                            if status_callback:
                                status_callback("Creating action plan...")
                        elif task_name == "create_action_plan":
                            if progress_callback:
                                progress_callback(95)
                            if status_callback:
                                status_callback("Generating final report...")
                    else:
                        logger.warning(f"Task {task_name} has output attribute but it is None")
                else:
                    logger.warning(f"Task {task_name} does not have an output attribute")
            
            # Verify task outputs exist
            if not task_outputs or len(task_outputs) == 0:
                logger.warning("No task outputs were generated. Attempting to extract from crew output.")
                # Try to get outputs from the crew output string
                crew_output_str = str(crew_output)
                
                # Generate fallback task outputs
                task_outputs = {
                    "classify_video": "Technology",  # Default category
                    "summarize_content": "Analysis results were not generated properly. Please try again with a different model or settings.",
                    "analyze_content": "Unable to generate analysis. Please try again with cache disabled.",
                    "create_action_plan": "Action plan could not be created. Please try again.",
                    "write_report": "Report generation failed. Please try again with cache disabled."
                }
                
                logger.warning("Using fallback task outputs until issue is resolved.")
            
            # Additional check to ensure no placeholder text in output
            placeholder_detected = False
            for key, value in task_outputs.items():
                if isinstance(value, str) and "placeholder" in value.lower():
                    logger.warning(f"Detected placeholder text in task output '{key}'. Analysis may have failed.")
                    placeholder_detected = True
                    break
            
            if placeholder_detected:
                logger.error("Analysis produced placeholder outputs, something went wrong")
                return None, "Analysis produced placeholder results. Please try again with cache disabled or a different model."
            
            # Update progress
            if progress_callback:
                progress_callback(100)
            if status_callback:
                status_callback("Analysis completed successfully!")
            
            # Get token usage
            token_usage = crew_output.token_usage if hasattr(crew_output, 'token_usage') else None
            
            # Extract category from classification output
            category = "Uncategorized"
            context_tag = "General"
            if "classify_video" in task_outputs:
                category = extract_category(task_outputs["classify_video"])
                context_tag = extract_context_tag(task_outputs["classify_video"])
            
            # Prepare results
            results = {
                "video_id": video_id,
                "youtube_url": youtube_url,
                "transcript": transcript,
                "timestamped_transcript": timestamped_transcript,
                "transcript_list": transcript_list,
                "output": str(crew_output),
                "task_outputs": task_outputs,
                "category": category,
                "context_tag": context_tag,
                "token_usage": token_usage,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "chat_details": chat_details
            }
            
            # Verify that we have actual analysis data before caching
            if not task_outputs or len(task_outputs) == 0:
                logger.error("Analysis produced no task outputs, something went wrong")
                return None, "Analysis produced no results. Please try again."
            
            # Cache the analysis results for future use
            try:
                # Create a deep copy to avoid modifying the original results
                results_to_cache = copy.deepcopy(results)
                logger.info(f"Caching analysis results for video {video_id}")
                cache_analysis(video_id, results_to_cache)
                logger.info(f"Successfully cached analysis results")
            except Exception as e:
                logger.warning(f"Error caching analysis results: {str(e)}")
            
            # Final validation check to ensure we're returning proper data
            if not isinstance(results, dict):
                logger.error(f"Results is not a dictionary: {type(results)}")
                # Create a minimal valid structure
                results = {
                    "video_id": video_id,
                    "youtube_url": youtube_url,
                    "transcript": transcript,
                    "task_outputs": task_outputs,
                    "category": category,
                    "context_tag": context_tag,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "chat_details": chat_details
                }
                logger.warning("Created fallback results dictionary")
            
            return results, None
            
        except Exception as crew_setup_error:
            logger.error(f"Error setting up or running crew: {str(crew_setup_error)}", exc_info=True)
            return None, f"Failed to set up or run analysis crew: {str(crew_setup_error)}"
        
    except Exception as e:
        logger.error(f"Error in analysis: {str(e)}", exc_info=True)
        return None, str(e)

def run_direct_analysis(youtube_url: str, plain_transcript: str, progress_callback=None, status_callback=None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Run the YouTube Analysis Crew directly with a provided transcript.
    This is used as a fallback when the standard run_analysis function encounters errors.
    
    Args:
        youtube_url: The URL of the YouTube video to analyze
        plain_transcript: The plain text transcript of the video
        progress_callback: Optional callback function to update progress (0-100)
        status_callback: Optional callback function to update status messages
        
    Returns:
        A tuple containing the analysis results and any error message
    """
    try:
        # Extract video ID for thumbnail
        video_id = extract_video_id(youtube_url)
        
        # Update progress
        if progress_callback:
            progress_callback(40)
        if status_callback:
            status_callback("Analyzing video content...")
        
        # Get video info
        video_info = get_video_info(youtube_url)
        
        # Create and run the crew
        import os
        
        # Get model and temperature from environment variables
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        temperature = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
        
        crew_instance = YouTubeAnalysisCrew(model_name=model, temperature=temperature)
        crew = crew_instance.crew()
        
        # Start the crew execution
        inputs = {"youtube_url": youtube_url, "transcript": plain_transcript}
        crew_output = crew.kickoff(inputs=inputs)
        
        # Extract task outputs
        task_outputs = {}
        for task in crew.tasks:
            if hasattr(task, 'output') and task.output:
                task_name = task.name if hasattr(task, 'name') else task.__class__.__name__
                task_outputs[task_name] = task.output.raw
                
                # Update progress based on task completion
                if task_name == "classify_video":
                    if progress_callback:
                        progress_callback(50)
                    if status_callback:
                        status_callback("Summarizing video content...")
                elif task_name == "summarize_content":
                    if progress_callback:
                        progress_callback(70)
                    if status_callback:
                        status_callback("Analyzing video content...")
                elif task_name == "analyze_content":
                    if progress_callback:
                        progress_callback(85)
                    if status_callback:
                        status_callback("Creating action plan...")
                elif task_name == "create_action_plan":
                    if progress_callback:
                        progress_callback(95)
                    if status_callback:
                        status_callback("Generating final report...")
        
        # Update progress
        if progress_callback:
            progress_callback(100)
        if status_callback:
            status_callback("Analysis completed successfully!")
        
        # Get token usage
        token_usage = crew_output.token_usage if hasattr(crew_output, 'token_usage') else None
        
        # Extract category from classification output
        category = "Uncategorized"
        context_tag = "General"
        if "classify_video" in task_outputs:
            category = extract_category(task_outputs["classify_video"])
            context_tag = extract_context_tag(task_outputs["classify_video"])
        
        # Prepare results
        results = {
            "video_id": video_id,
            "youtube_url": youtube_url,
            "transcript": plain_transcript,
            "output": str(crew_output),
            "task_outputs": task_outputs,
            "category": category,
            "context_tag": context_tag,
            "token_usage": token_usage,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "chat_details": None
        }
        
        # Cache the analysis results for future use
        try:
            # Create a deep copy to avoid modifying the original results
            results_to_cache = copy.deepcopy(results)
            cache_analysis(video_id, results_to_cache)
        except Exception as e:
            logger.warning(f"Error caching analysis results: {str(e)}")
        
        return results, None
        
    except Exception as e:
        logger.error(f"Error in direct analysis: {str(e)}", exc_info=True)
        return None, str(e) 