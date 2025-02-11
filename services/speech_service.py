from enum import Enum
from proxies import azure_proxy

class VoiceGender(Enum):
    MALE = "male"
    FEMALE = "female"

def synthesize_speech(
    text: str, 
    gender: VoiceGender = VoiceGender.MALE,
    rate: float = 1.0,
    output_path: str = "output.mp3",
) -> None:
    if gender == VoiceGender.MALE:
        voice_variation = azure_proxy.VoiceVariation.MALE
    else:
        voice_variation = azure_proxy.VoiceVariation.FEMALE
    return azure_proxy.synthesize_speech(text=text, voice_variation=voice_variation, rate=rate, output_path=output_path)