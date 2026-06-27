"""Seed the database with demo data: admin user, Operations bot, 3 preset skills, demo provider."""
import asyncio, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

async def seed():
    from cante.auth import hash_password
    from cante.db import async_session_factory
    from cante.models import Bot, Number, Provider, Route, Secret, Skill, User

    async with async_session_factory() as session:
        # Admin user
        from sqlalchemy import select
        existing = (await session.execute(select(User).where(User.email == "admin@example.com"))).scalar_one_or_none()
        if not existing:
            session.add(User(email="admin@example.com", hashed_password=hash_password("change-me"), role="admin"))
            print("✓ Admin user created")

        # Demo provider (uses ANTHROPIC_API_KEY from env)
        existing = (await session.execute(select(Provider).where(Provider.name == "Claude (Anthropic)"))).scalar_one_or_none()
        if not existing:
            session.add(Provider(
                name="Claude (Anthropic)", type="anthropic", base_url="https://api.anthropic.com/v1",
                model="claude-sonnet-4-20250514", api_key_ref="ANTHROPIC_API_KEY",
                params={"temperature": 0.7, "max_tokens": 4096},
            ))
            print("✓ Demo provider created")

        # Preset: Operations (default triage/FAQ assistant)
        existing = (await session.execute(select(Skill).where(Skill.name == "Operations (Default)"))).scalar_one_or_none()
        if not existing:
            session.add(Skill(
                name="Operations (Default)", preset="operations", language_default="en",
                playbook_md="## Who you are\nYou are a helpful triage and FAQ assistant for a business.\n\n## What you can do\n- Answer common questions\n- Clarify what the user needs\n- Escalate complex requests to a human\n- Close the conversation when resolved\n\n## Tone\nFriendly, professional, concise.",
                guardrails_md="Only discuss topics related to the business. Politely redirect off-topic questions.",
                scope={"in": ["hours","services","pricing","contact","help"], "out_policy": "redirect_then_escalate", "max_offscope_turns": 2},
                tools={"builtin": ["lookup_or_create_contact","close_conversation","escalate_to_human"], "declared": []},
                done_condition="The user's question is fully answered or they explicitly say goodbye.",
                escalation={"on": ["explicit_request","scope_exhausted","circuit_breaker"], "message": "Let me get a human colleague to help."},
            ))
            print("✓ Operations skill created")

        # Preset: Barber
        existing = (await session.execute(select(Skill).where(Skill.name == "Barber Shop Front Desk"))).scalar_one_or_none()
        if not existing:
            session.add(Skill(
                name="Barber Shop Front Desk", preset="barber", language_default="en",
                playbook_md="## Who you are\nYou are the front desk of a barber shop. You help customers book, cancel, and inquire about appointments.\n\n## What you can do\n- Check available slots\n- Book appointments\n- Cancel appointments\n- Answer questions about services and prices\n\n## Tone\nCasual, friendly, like a neighborhood barber.",
                guardrails_md="Only discuss appointments, services, hours, prices, and location. Politely refuse anything else.",
                tools={"builtin": ["lookup_or_create_contact","close_conversation","escalate_to_human"], "declared": [
                    {"name":"get_open_slots","description":"Check available appointment slots for a date","input_schema":{"type":"object","properties":{"date":{"type":"string"}},"required":["date"]},"http":{"method":"GET","url":"http://mock-backend:9000/availability?date={date}","headers":{},"timeout_s":10},"response_mapping":"json"},
                    {"name":"book_appointment","description":"Book an appointment","input_schema":{"type":"object","properties":{"date":{"type":"string"},"time":{"type":"string"},"name":{"type":"string"}},"required":["date","time","name"]},"http":{"method":"POST","url":"http://mock-backend:9000/appointments","headers":{"Content-Type":"application/json"},"timeout_s":10},"response_mapping":"json"},
                ]},
                done_condition="An appointment is confirmed.",
                escalation={"on":["explicit_request"],"message":"Let me transfer you to the barber."},
            ))
            print("✓ Barber skill created")

        # Preset: Trainer
        existing = (await session.execute(select(Skill).where(Skill.name == "Youth Sports Trainer"))).scalar_one_or_none()
        if not existing:
            session.add(Skill(
                name="Youth Sports Trainer", preset="trainer", language_default="en",
                playbook_md="## Who you are\nYou are a youth sports coach's assistant. You help parents with schedules, absences, and game info.\n\n## What you can do\n- Check the game schedule\n- Report a player's absence\n- Send messages to parents\n\n## Tone\nEncouraging, clear, team-spirited.",
                tools={"builtin": ["lookup_or_create_contact","close_conversation","escalate_to_human"], "declared": [
                    {"name":"get_schedule","description":"Get the team game schedule","input_schema":{"type":"object","properties":{"team":{"type":"string"}},"required":["team"]},"http":{"method":"GET","url":"http://mock-backend:9000/schedule?team={team}","headers":{},"timeout_s":10},"response_mapping":"json"},
                    {"name":"report_absence","description":"Report a player's absence","input_schema":{"type":"object","properties":{"player_name":{"type":"string"},"date":{"type":"string"},"reason":{"type":"string"}},"required":["player_name","date"]},"http":{"method":"POST","url":"http://mock-backend:9000/absences","headers":{"Content-Type":"application/json"},"timeout_s":10},"response_mapping":"json"},
                ]},
                done_condition="The parent's question is answered or the absence is reported.",
                escalation={"on":["explicit_request"],"message":"Let me get the coach to help you directly."},
            ))
            print("✓ Trainer skill created")

        await session.commit()
        print("\nSeed complete. Login: admin@example.com / change-me")

if __name__ == "__main__":
    asyncio.run(seed())
