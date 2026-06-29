from __future__ import annotations

from vtx_claw.config.schema import ClawConfig, save_claw_config


def run_onboard() -> None:
    print("\n=== VTX Claw Setup Wizard ===\n")

    config = ClawConfig()

    print("1. Gateway Configuration")
    host = input(f"   Host [{config.gateway.host}]: ").strip()
    if host:
        config.gateway.host = host
    port = input(f"   Port [{config.gateway.port}]: ").strip()
    if port:
        config.gateway.port = int(port)

    print("\n2. LLM Provider")
    print("   Supported: openai, anthropic, deepseek, gemini, grok, ollama, custom")
    current_provider = config.llm.provider or "openai"
    provider = input(f"   Provider [{current_provider}]: ").strip() or current_provider
    config.llm.provider = provider

    # Select default model based on provider
    default_model = "gpt-4o"
    if provider == "anthropic":
        default_model = "claude-sonnet-4-20250514"
    elif provider == "deepseek":
        default_model = "deepseek-chat"
    elif provider == "gemini":
        default_model = "gemini-2.0-flash"
    elif provider == "grok":
        default_model = "grok-3"
    elif provider == "ollama":
        default_model = "llama3"

    current_model = config.llm.default_model or default_model
    model = input(f"   Model [{current_model}]: ").strip() or current_model
    config.llm.default_model = model

    # Save selection to vtx's last selected settings
    from vtx.config import set_last_selected

    set_last_selected(model_id=model, provider=provider, thinking_level="high")

    # Handle API keys
    from vtx.llm.oauth.dynamic import get_dynamic_api_key, save_api_key

    if provider not in ("ollama", "custom"):
        existing_key = get_dynamic_api_key(provider)
        key_prompt = "   API Key"
        if existing_key:
            key_prompt += " [already set, press Enter to keep]"
        key_prompt += ": "

        api_key = input(key_prompt).strip()
        if api_key:
            save_api_key(provider, api_key)
            # Also store it in claw config for local backup
            prov_block = getattr(config.llm, provider, None)
            if isinstance(prov_block, dict):
                prov_block["api_key"] = api_key
        elif not existing_key:
            print(f"   [Warning] No API key set for {provider}. You may need to set one later.")
    elif provider == "custom":
        base_url = input("   Base URL for custom provider: ").strip()
        if base_url:
            config.llm.custom["base_url"] = base_url
        custom_model = input("   Model name for custom provider: ").strip()
        if custom_model:
            config.llm.custom["model"] = custom_model
            config.llm.default_model = custom_model
        api_key = input("   API Key for custom provider: ").strip()
        if api_key:
            config.llm.custom["api_key"] = api_key

    print("\n3. Channel Setup")
    print("   Available: telegram, feishu, discord, whatsapp")

    tg = input("   Enable Telegram? [y/N]: ").strip().lower()
    if tg == "y":
        config.channels.telegram.enabled = True
        config.channels.telegram.bot_token = input("   Telegram Bot Token: ").strip()

    fs = input("   Enable Feishu? [y/N]: ").strip().lower()
    if fs == "y":
        config.channels.feishu.enabled = True
        config.channels.feishu.app_id = input("   Feishu App ID: ").strip()
        config.channels.feishu.app_secret = input("   Feishu App Secret: ").strip()

    dc = input("   Enable Discord? [y/N]: ").strip().lower()
    if dc == "y":
        config.channels.discord.enabled = True
        config.channels.discord.bot_token = input("   Discord Bot Token: ").strip()

    print("\n4. Authentication")
    print("   Policies: pairing, allowlist, open")
    policy = input("   Default policy [pairing]: ").strip() or "pairing"
    config.auth.default_policy = policy

    print("\n5. Cron Scheduler")
    cron = input("   Enable cron jobs? [y/N]: ").strip().lower()
    if cron == "y":
        config.cron.enabled = True

    print("\n6. Docker Sandbox")
    sandbox = input("   Enable Docker sandbox for sub-agents? [y/N]: ").strip().lower()
    if sandbox == "y":
        config.sandbox.enabled = True
        img = input(f"   Docker image [{config.sandbox.image}]: ").strip()
        if img:
            config.sandbox.image = img

    save_claw_config(config)
    print("\n✓ Config saved to ~/.vtx/claw.yml")
    print("\nStart with: vtx-claw start")
    print(f"  Web UI:   http://{config.gateway.host}:{config.gateway.port}/\n")
