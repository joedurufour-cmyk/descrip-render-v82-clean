from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from enum import Enum


class PhysiqueLevel(str, Enum):
    LEAN = "lean"
    DEFINED = "defined"
    ULTRA = "ultra"


class StylePreset(str, Enum):
    PHOTOREALISTIC = "photorealistic"
    CINEMATIC = "cinematic"
    CGI = "cgi"
    COMIC = "comic"
    ANIME = "anime"
    WILDCARD = "wildcard"


class TransformRequest(BaseModel):
    physique: PhysiqueLevel = Field(..., description="Nivel de transformacion fisica")
    packs: int = Field(8, ge=4, le=12, description="Numero de abdominales visibles")
    low_waist: bool = Field(True, description="Pantalon/cintura baja para exposicion")
    feminine: bool = Field(False, description="Enfasis en curvas femeninas exuberantes")
    pose_variation: bool = Field(False, description="Variar poses entre prompts")
    lighting_drama: bool = Field(False, description="Enfasis en iluminacion escultorica")

    class Config:
        json_schema_extra = {
            "example": {
                "physique": "ultra",
                "packs": 8,
                "low_waist": True,
                "feminine": True,
                "pose_variation": True,
                "lighting_drama": True
            }
        }


class GeneratedPrompt(BaseModel):
    style_label: str
    prompt_text: str
    parameters: dict
    preservation_score: float = Field(..., ge=0.0, le=1.0)
    visual_power_score: float = Field(..., ge=0.0, le=1.0)


class SourceAnalysis(BaseModel):
    subject: str
    identity: str
    physique_original: str
    pose: str
    expression: str
    clothing: str
    environment: str
    camera: str
    lighting: str
    style: str


class GenerationResponse(BaseModel):
    source_analysis: SourceAnalysis
    locked_attributes: List[str]
    mutable_attributes: List[str]
    transformation_applied: dict
    prompts: List[GeneratedPrompt]
