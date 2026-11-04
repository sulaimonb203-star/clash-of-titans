def generate_native_table(stats, title="🏆 RESULTS 🏆"):
    # Sort by score descending, then by time ascending
    sorted_stats = sorted(stats.values(), key=lambda x: (-x.get('score', 0), x.get('time_seconds', 9999)))
    
    # Native GFM Markdown Table
    markdown = f"### {title}\n\n"
    markdown += "| Name | Sc | Ans | Mis | Time | % | /400 | Aura |\n"
    markdown += "|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|\n"
    
    for p in sorted_stats:
        name = p.get('name', 'Unknown')[:12]
        score = p.get('score', 0)
        answered = p.get('answered', 0)
        missed = p.get('missed', 0)
        time_str = p.get('time_str', '0s')
        percent = f"{p.get('percent', 0):.1f}%"
        real_score = p.get('real_score', 0)
        aura = p.get('aura', 0)
        
        markdown += f"| {name} | {score} | {answered} | {missed} | {time_str} | {percent} | {real_score} | +{aura} |\n"
        
    return markdown

async def send_native_table(bot, chat_id, markdown_table, fallback_text):
    """Sends a native Telegram Rich Message table, falls back to <pre> if unsupported."""
    try:
        payload = {
            "chat_id": chat_id,
            "rich_message": {"markdown": markdown_table}
        }
        # Call the raw Bot API 10.1+ method
        await bot._post("sendRichMessage", data=payload)
    except Exception as e:
        print(f"Native table failed: {e}. Falling back to <pre>.")
        await bot.send_message(chat_id, f"<pre>{fallback_text}</pre>", parse_mode="HTML")
