#!/usr/bin/env python3
"""Test script that tests all TTS providers by making them say 'Hello'."""

import warnings
# Suppress torch warnings before any imports that might load torch
warnings.filterwarnings("ignore", category=FutureWarning, module="torch")
warnings.filterwarnings("ignore", category=UserWarning, module="torch")

import sys
import os
import time
import subprocess
from typing import Dict, List, Tuple, Optional

# Add parent directory to path to import talkito modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from talkito.tts import TTS_PROVIDERS, detect_tts_engine, create_tts_provider


def test_system_tts_providers() -> List[Tuple[str, bool, str]]:
    """Test system TTS providers (say, espeak, festival, flite)."""
    results = []
    
    # Detect available system TTS engine
    detected_engine = detect_tts_engine()
    
    system_engines = ["say", "espeak", "festival", "flite"]
    
    for engine in system_engines:
        print(f"Testing {engine}... ", end="", flush=True)
        
        available = subprocess.run(
            ["which", engine], 
            capture_output=True, 
            text=True
        ).returncode == 0
        
        if not available:
            print("❌ Not installed")
            results.append((engine, False, "Not installed"))
            continue
            
        try:
            # Test the engine
            if engine == "say":
                cmd = ["say", "Hello"]
            elif engine == "espeak":
                cmd = ["espeak", "Hello"]
            elif engine == "festival":
                # Festival uses stdin
                result = subprocess.run(
                    ["festival", "--tts"],
                    input="Hello",
                    text=True,
                    capture_output=True,
                    timeout=10
                )
                print("✅ Pass" if result.returncode == 0 else "❌ Failed")
                results.append((engine, result.returncode == 0, "Tested via stdin"))
                continue
            elif engine == "flite":
                # Flite uses stdin
                result = subprocess.run(
                    ["flite", "-voice", "slt"],
                    input="Hello",
                    text=True,
                    capture_output=True,
                    timeout=10
                )
                print("✅ Pass" if result.returncode == 0 else "❌ Failed")
                results.append((engine, result.returncode == 0, "Tested via stdin"))
                continue
            
            # For say and espeak, run with timeout
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            success = result.returncode == 0
            print("✅ Pass" if success else "❌ Failed")
            results.append((engine, success, "Tested successfully"))
            
            # Small delay between tests
            time.sleep(1)
            
        except subprocess.TimeoutExpired:
            print("❌ Timeout")
            results.append((engine, False, "Timeout"))
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            results.append((engine, False, f"Error: {str(e)}"))
    
    return results


def test_cloud_tts_providers() -> List[Tuple[str, bool, str]]:
    """Test cloud TTS providers."""
    results = []
    
    for provider_name, provider_info in TTS_PROVIDERS.items():
        # Skip alias
        if provider_name == "polly":
            continue
            
        display_name = provider_info.get('display_name', provider_name)
        print(f"Testing {display_name}... ", end="", flush=True)
        
        # Try to create provider instance using new factory approach
        try:
            provider = create_tts_provider(provider_name)
            if provider is None:
                print("❌ Not initialized")
                results.append((display_name, False, "Provider class not found"))
                continue
        except Exception as e:
            print("❌ Provider creation failed")
            results.append((display_name, False, f"Provider creation failed: {str(e)[:50]}"))
            continue
        
        # Check environment variables or credentials
        env_var = provider_info.get('env_var')
        if env_var and not os.environ.get(env_var):
            print(f"❌ Missing {env_var}")
            results.append((display_name, False, f"Missing {env_var}"))
            continue
        elif provider_name == 'aws':
            # AWS has special credential handling
            has_creds = (
                os.environ.get('AWS_ACCESS_KEY_ID') and os.environ.get('AWS_SECRET_ACCESS_KEY')
            ) or os.path.exists(os.path.expanduser('~/.aws/credentials'))
            if not has_creds:
                print("❌ Missing AWS credentials")
                results.append((display_name, False, "Missing AWS credentials"))
                continue
        elif provider_name == 'gcloud':
            # Google Cloud uses service account JSON
            gcp_creds = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
            if not gcp_creds or not os.path.exists(gcp_creds):
                print("❌ Missing credentials file")
                results.append((display_name, False, "Missing GOOGLE_APPLICATION_CREDENTIALS file"))
                continue
        
        # Try to call the provider's speak method
        try:
            success = provider.speak("Hello")
            print("✅ Pass" if success else "❌ Failed")
            results.append((display_name, success, "Tested successfully" if success else "Failed to speak"))
            
            # Small delay between tests
            time.sleep(2)
            
        except ImportError as e:
            install_cmd = provider_info.get('install', 'Unknown')
            print(f"❌ Import error: {str(e)[:100]}")
            results.append((display_name, False, f"Import error - try: {install_cmd}"))
        except Exception as e:
            print(f"❌ Error: {str(e)[:100]}")
            results.append((display_name, False, f"Error: {str(e)[:100]}"))
    
    return results


def main():
    """Main test function."""
    print("🎤 Testing all TTS providers with 'Hello'\n")
    
    # Test system TTS providers
    system_results = test_system_tts_providers()
    
    print()  # Add space between system and cloud tests
    
    # Test cloud/API TTS providers
    cloud_results = test_cloud_tts_providers()
    
    # Summary
    all_results = system_results + cloud_results
    total_tests = len(all_results)
    passed_tests = sum(1 for _, success, _ in all_results if success)
    
    print(f"\n📊 Results: {passed_tests}/{total_tests} providers working")
    
    if passed_tests > 0:
        print(f"🎉 {passed_tests} TTS provider(s) are ready to use!")
    else:
        print("⚠️  No TTS providers are working. Check credentials and installations.")


if __name__ == "__main__":
    main()