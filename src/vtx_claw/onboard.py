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
    print("   Supported: openai, anthropic, deepseek, ollama, custom")
    input("   Provider [openai]: ").strip() or "openai"
    input("   Model [gpt-4o]: ").strip() or "gpt-4o"

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
