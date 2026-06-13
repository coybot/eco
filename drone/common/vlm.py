"""
VLM Service - Vision-Language Model for drone perception and reasoning.

Uses Qwen3-VL to:
1. Understand what the drone sees (scene description)
2. Make decisions about what to do next (action selection)
3. Handle subjective judgments (e.g., "prettiest tree")

The VLM outputs structured actions that integrate with Nav2 for execution.
"""

import os
import sys
import time
import json
import base64
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

# Models directory (install: ~/drone-api/models; repo: drone/models)
_script_dir = Path(__file__).parent.resolve()
MODELS_DIR = _script_dir / "models" if (_script_dir / "models").exists() else _script_dir.parent / "models"

# Symlinks created by setup_models.py
VLM_MODEL_PATH = MODELS_DIR / "vlm.gguf"
VLM_MMPROJ_PATH = MODELS_DIR / "vlm_mmproj.gguf"


class ActionType(str, Enum):
    """Types of actions the VLM can output."""
    NAVIGATE_TO_POINT = "navigate_to_point"  # Point on image -> Nav2 goal
    NAVIGATE_TO_OBJECT = "navigate_to_object"  # Named object -> Nav2 goal
    CAPTURE_PHOTO = "capture_photo"
    REPORT = "report"  # Send message to user
    PHASE_COMPLETE = "phase_complete"
    MISSION_COMPLETE = "mission_complete"
    MISSION_FAILED = "mission_failed"
    ASK_CLOUD = "ask_cloud"  # Need help from cloud


@dataclass
class VLMAction:
    """Structured action output from VLM."""
    action_type: ActionType
    
    # For navigate actions
    point_x: Optional[int] = None  # Pixel x coordinate
    point_y: Optional[int] = None  # Pixel y coordinate
    target_object: Optional[str] = None  # Object name to navigate to
    
    # For report/complete actions
    message: Optional[str] = None
    
    # VLM's reasoning (for debugging/display)
    reasoning: Optional[str] = None
    
    # Confidence (0-1)
    confidence: float = 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "point_x": self.point_x,
            "point_y": self.point_y,
            "target_object": self.target_object,
            "message": self.message,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'VLMAction':
        return cls(
            action_type=ActionType(data.get("action_type", "report")),
            point_x=data.get("point_x"),
            point_y=data.get("point_y"),
            target_object=data.get("target_object"),
            message=data.get("message"),
            reasoning=data.get("reasoning"),
            confidence=data.get("confidence", 1.0),
        )


# System prompt for the VLM
VLM_SYSTEM_PROMPT = """You are a drone pilot AI with vision. You see through the drone's camera and decide what to do next.

Your job is to accomplish the given mission by analyzing what you see and selecting the best action.

OUTPUT FORMAT:
You must respond with a JSON object containing:
{
  "reasoning": "Your brief reasoning about what you see and why you chose this action",
  "action_type": "one of: navigate_to_point, navigate_to_object, capture_photo, report, phase_complete, mission_complete, mission_failed, ask_cloud",
  "point_x": <pixel x coordinate if navigate_to_point>,
  "point_y": <pixel y coordinate if navigate_to_point>,
  "target_object": "<object name if navigate_to_object>",
  "message": "<message content if report/complete/failed>"
}

ACTION TYPES:
- navigate_to_point: Point to where the drone should fly (x,y pixel coordinates on the image)
- navigate_to_object: Navigate toward a detected object by name
- capture_photo: Take a photo of what's currently in view
- report: Send a message/observation to the user
- phase_complete: Current mission phase is done, move to next
- mission_complete: Entire mission accomplished successfully
- mission_failed: Cannot complete mission (explain why)
- ask_cloud: Need help from cloud AI (complex decision)

IMPORTANT:
- Be concise in reasoning
- For navigation, prefer pointing to specific locations on the image
- Consider obstacles and safety
- Report interesting findings
- Complete phases systematically before moving on"""


class VLMService:
    """
    Vision-Language Model service for drone perception and reasoning.
    
    Uses llama.cpp with Qwen3-VL for multimodal inference.
    """
    
    def __init__(self, model_path: Path = None, mmproj_path: Path = None):
        """
        Initialize VLM service.
        
        Args:
            model_path: Path to VLM GGUF file (default: models/vlm.gguf)
            mmproj_path: Path to vision encoder GGUF file
        """
        self.model_path = model_path or VLM_MODEL_PATH
        self.mmproj_path = mmproj_path or VLM_MMPROJ_PATH
        
        self._llm = None
        self._available = False
        
        self._init_model()
    
    def _init_model(self):
        """Initialize the VLM model."""
        if not self.model_path.exists():
            print(f"VLM model not found at {self.model_path}")
            print("Run setup_models.py to download Qwen3-VL")
            self._available = False
            return
        
        if not self.mmproj_path.exists():
            print(f"VLM vision encoder not found at {self.mmproj_path}")
            self._available = False
            return
        
        try:
            from llama_cpp import Llama
            from llama_cpp.llama_chat_format import Llava16ChatHandler
            
            print(f"Loading VLM: {self.model_path.name}...")
            
            # Create chat handler for vision
            chat_handler = Llava16ChatHandler(
                clip_model_path=str(self.mmproj_path),
                verbose=False,
            )
            
            # Load model
            self._llm = Llama(
                model_path=str(self.model_path),
                chat_handler=chat_handler,
                n_ctx=8192,  # Context window
                n_threads=4,
                n_gpu_layers=-1,  # Use all GPU layers
                verbose=False,
            )
            
            self._available = True
            print(f"VLM loaded successfully: {self.model_path.name}")
            
        except ImportError:
            print("llama-cpp-python not installed. Install with: pip install llama-cpp-python")
            self._available = False
        except Exception as e:
            print(f"Failed to load VLM: {e}")
            self._available = False
    
    def is_available(self) -> bool:
        """Check if VLM is available."""
        return self._available
    
    def _encode_image(self, image_path: str = None, image_bytes: bytes = None, 
                      image_array = None) -> str:
        """
        Encode image to base64 data URI.
        
        Args:
            image_path: Path to image file
            image_bytes: Raw image bytes
            image_array: NumPy array (RGB)
        
        Returns:
            Base64 data URI string
        """
        import cv2
        
        if image_array is not None:
            # Convert numpy array to JPEG bytes
            if len(image_array.shape) == 3 and image_array.shape[2] == 3:
                # RGB -> BGR for cv2
                bgr = cv2.cvtColor(image_array, cv2.COLOR_RGB2BGR)
            else:
                bgr = image_array
            _, buffer = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            image_bytes = buffer.tobytes()
        elif image_path:
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
        
        if image_bytes is None:
            raise ValueError("No image provided")
        
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        return f"data:image/jpeg;base64,{b64}"
    
    def decide(
        self,
        image,  # Can be path, bytes, or numpy array
        mission_phase: Dict[str, Any],
        drone_state: Dict[str, Any] = None,
        history: list = None,
    ) -> VLMAction:
        """
        Decide what action to take based on current view and mission.
        
        Args:
            image: Current camera frame (path, bytes, or numpy array)
            mission_phase: Current mission phase with objective, success criteria
            drone_state: Optional drone state (position, battery, etc.)
            history: Optional recent action history
        
        Returns:
            VLMAction describing what to do next
        """
        if not self._available:
            return VLMAction(
                action_type=ActionType.MISSION_FAILED,
                message="VLM not available",
                reasoning="VLM model not loaded"
            )
        
        # Encode image
        try:
            image_uri = self._encode_image(
                image_path=image if isinstance(image, str) else None,
                image_bytes=image if isinstance(image, bytes) else None,
                image_array=image if hasattr(image, 'shape') else None,
            )
        except Exception as e:
            return VLMAction(
                action_type=ActionType.MISSION_FAILED,
                message=f"Failed to encode image: {e}",
                reasoning="Image encoding error"
            )
        
        # Build prompt
        prompt = self._build_prompt(mission_phase, drone_state, history)
        
        # Call VLM
        try:
            start_time = time.time()
            
            response = self._llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": VLM_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_uri}},
                            {"type": "text", "text": prompt},
                        ],
                    },
                ],
                max_tokens=500,
                temperature=0.1,
            )
            
            elapsed = time.time() - start_time
            
            # Parse response
            content = response['choices'][0]['message']['content']
            action = self._parse_response(content)

            print(f"VLM decision in {elapsed:.2f}s: {action.action_type.value}")

            # Optional training-data capture (no-op unless a recorder is enabled).
            try:
                from data_recorder import get_default
                recorder = get_default()
                if recorder.enabled:
                    recorder.record_vlm(
                        rgb_frame=image if hasattr(image, 'shape') else None,
                        system_prompt=VLM_SYSTEM_PROMPT,
                        user_prompt=prompt,
                        response=content,
                        action=action.to_dict(),
                        inference_ms=elapsed * 1000.0,
                        drone_state=drone_state,
                    )
            except Exception:
                pass

            return action
            
        except Exception as e:
            print(f"VLM inference error: {e}")
            return VLMAction(
                action_type=ActionType.ASK_CLOUD,
                message=f"VLM error: {e}",
                reasoning="Inference failed, need cloud assistance"
            )
    
    def _build_prompt(
        self,
        mission_phase: Dict[str, Any],
        drone_state: Dict[str, Any] = None,
        history: list = None,
    ) -> str:
        """Build the prompt for the VLM."""
        lines = [
            "CURRENT MISSION PHASE:",
            f"  Objective: {mission_phase.get('objective', 'Unknown')}",
        ]
        
        if mission_phase.get('success'):
            lines.append(f"  Success when: {mission_phase.get('success')}")
        
        if mission_phase.get('evaluation_criteria'):
            lines.append(f"  Evaluation criteria: {mission_phase.get('evaluation_criteria')}")
        
        if drone_state:
            lines.append("")
            lines.append("DRONE STATE:")
            if drone_state.get('battery'):
                lines.append(f"  Battery: {drone_state['battery']}%")
            if drone_state.get('position'):
                lines.append(f"  Position: {drone_state['position']}")
            if drone_state.get('altitude'):
                lines.append(f"  Altitude: {drone_state['altitude']}m")
        
        if history:
            lines.append("")
            lines.append("RECENT ACTIONS:")
            for h in history[-5:]:
                lines.append(f"  - {h}")
        
        lines.append("")
        lines.append("Based on what you see in the image and the mission objective, what should the drone do next?")
        lines.append("Respond with a JSON object.")
        
        return "\n".join(lines)
    
    def _parse_response(self, content: str) -> VLMAction:
        """Parse VLM response into structured action."""
        # Try to extract JSON from response
        try:
            # Handle markdown code blocks
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end]
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end]
            
            data = json.loads(content.strip())
            return VLMAction.from_dict(data)
            
        except json.JSONDecodeError:
            # Fallback: try to extract action from text
            content_lower = content.lower()
            
            if "mission_complete" in content_lower or "mission complete" in content_lower:
                return VLMAction(
                    action_type=ActionType.MISSION_COMPLETE,
                    message=content[:200],
                    reasoning="Parsed from text response"
                )
            elif "phase_complete" in content_lower or "phase complete" in content_lower:
                return VLMAction(
                    action_type=ActionType.PHASE_COMPLETE,
                    message=content[:200],
                    reasoning="Parsed from text response"
                )
            elif "failed" in content_lower or "cannot" in content_lower:
                return VLMAction(
                    action_type=ActionType.MISSION_FAILED,
                    message=content[:200],
                    reasoning="Parsed from text response"
                )
            else:
                # Default to report
                return VLMAction(
                    action_type=ActionType.REPORT,
                    message=content[:500],
                    reasoning="Could not parse structured action"
                )
    
    def describe_scene(self, image) -> str:
        """
        Get a natural language description of what's in the image.
        
        Args:
            image: Camera frame (path, bytes, or numpy array)
        
        Returns:
            Scene description string
        """
        if not self._available:
            return "VLM not available"
        
        try:
            image_uri = self._encode_image(
                image_path=image if isinstance(image, str) else None,
                image_bytes=image if isinstance(image, bytes) else None,
                image_array=image if hasattr(image, 'shape') else None,
            )
            
            response = self._llm.create_chat_completion(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_uri}},
                            {"type": "text", "text": "Describe what you see in this image briefly. Focus on objects, distances, and spatial layout relevant for drone navigation."},
                        ],
                    },
                ],
                max_tokens=300,
                temperature=0.1,
            )
            
            return response['choices'][0]['message']['content']
            
        except Exception as e:
            return f"Error describing scene: {e}"
    
    def evaluate(self, image, criteria: str) -> Tuple[float, str]:
        """
        Evaluate something in the image based on criteria.
        
        Args:
            image: Camera frame
            criteria: What to evaluate (e.g., "aesthetic beauty of the tree")
        
        Returns:
            Tuple of (score 0-10, explanation)
        """
        if not self._available:
            return 0.0, "VLM not available"
        
        try:
            image_uri = self._encode_image(
                image_path=image if isinstance(image, str) else None,
                image_bytes=image if isinstance(image, bytes) else None,
                image_array=image if hasattr(image, 'shape') else None,
            )
            
            response = self._llm.create_chat_completion(
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_uri}},
                            {"type": "text", "text": f"Evaluate what you see based on: {criteria}\n\nRespond with JSON: {{\"score\": <0-10>, \"explanation\": \"<brief reason>\"}}"},
                        ],
                    },
                ],
                max_tokens=200,
                temperature=0.1,
            )
            
            content = response['choices'][0]['message']['content']
            
            # Parse response
            try:
                if "```" in content:
                    start = content.find("{")
                    end = content.rfind("}") + 1
                    content = content[start:end]
                data = json.loads(content)
                return float(data.get("score", 5)), data.get("explanation", "")
            except:
                return 5.0, content[:200]
                
        except Exception as e:
            return 0.0, f"Error evaluating: {e}"
    
    def get_info(self) -> Dict[str, Any]:
        """Get information about the loaded VLM."""
        return {
            "available": self._available,
            "model_path": str(self.model_path) if self.model_path.exists() else None,
            "mmproj_path": str(self.mmproj_path) if self.mmproj_path.exists() else None,
        }


# Singleton instance
_vlm_service = None

def get_vlm_service() -> VLMService:
    """Get or create the VLM service singleton."""
    global _vlm_service
    if _vlm_service is None:
        _vlm_service = VLMService()
    return _vlm_service


# Test function
def test_vlm():
    """Test the VLM service."""
    print("Testing VLM Service...")
    
    vlm = get_vlm_service()
    print(f"Info: {vlm.get_info()}")
    
    if not vlm.is_available():
        print("VLM not available - skipping inference test")
        return
    
    # Test with a sample mission
    mission_phase = {
        "objective": "Find and photograph the most interesting object in view",
        "success": "Photo taken of selected object",
    }
    
    print("\nTest requires a camera frame - skipping inference test")
    print("VLM service initialized successfully")


if __name__ == "__main__":
    test_vlm()
