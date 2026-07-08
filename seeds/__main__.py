"""Seed the database with demo data: admin user, Operations bot, 3 preset skills, demo provider.

Declared HTTP tools and the ``allowed_hosts`` field
----------------------------------------------------
Every declared HTTP tool runs through an SSRF egress filter
(``is_safe_url`` in ``cante.security``). The filter blocks requests to
internal / loopback / link-local / metadata addresses **unless** the
target host is explicitly listed in ``allowed_hosts``.

Configure ``allowed_hosts`` at the tool level::

    {
      "name": "get_open_slots",
      "http": { "method": "GET", "url": "http://mock-backend:9000/...",
                "allowed_hosts": ["mock-backend"] }
    }

Or at the skill level (fallback for every declared tool in that skill)::

    { "tools": { "builtin": [...], "declared": [...],
                 "allowed_hosts": ["mock-backend"] } }

Without ``allowed_hosts``, tools can only reach public internet hosts
that pass the default is_safe_url checks.
"""
import asyncio
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

async def seed():
    from cante.auth import hash_password
    from cante.db import async_session_factory
    from cante.models import SEEDED_TENANT, Bot, Number, Provider, Route, Skill, User
    from cante.settings import settings
    from cante.tenant import with_tenant
    from sqlalchemy import select

    def booking_tools():
        """Two HTTP tools every appointment-based front desk shares: check open
        slots and book an appointment, both against the demo mock-backend."""
        return {
            "builtin": ["lookup_or_create_contact", "close_conversation", "escalate_to_human"],
            "declared": [
                {"name": "get_open_slots", "description": "Check available appointment slots for a date", "input_schema": {"type": "object", "properties": {"date": {"type": "string"}}, "required": ["date"]}, "http": {"method": "GET", "url": "http://mock-backend:9000/availability?date={date}", "headers": {}, "timeout_s": 10, "allowed_hosts": ["mock-backend"]}, "response_mapping": "json"},
                {"name": "book_appointment", "description": "Book an appointment", "input_schema": {"type": "object", "properties": {"date": {"type": "string"}, "time": {"type": "string"}, "name": {"type": "string"}}, "required": ["date", "time", "name"]}, "http": {"method": "POST", "url": "http://mock-backend:9000/appointments", "headers": {"Content-Type": "application/json"}, "timeout_s": 10, "allowed_hosts": ["mock-backend"]}, "response_mapping": "json"},
            ],
        }

    if settings.admin_password == "change-me":
        raise SystemExit(
            "Refusing to seed: ADMIN_PASSWORD is still the shipped default. "
            "Set ADMIN_EMAIL / ADMIN_PASSWORD in .env before running `make seed`."
        )

    admin_email = settings.admin_email
    admin_password = settings.admin_password

    async with async_session_factory() as session:
        # Seed data lives in the seeded tenant; wrap writes/reads so the
        # data-layer tenant enforcement (active once cante.tenant is imported)
        # has a context.
        with with_tenant(SEEDED_TENANT):
            # Admin user (from settings, not hardcoded defaults)
            existing = (await session.execute(select(User).where(User.email == admin_email))).scalar_one_or_none()
            if not existing:
                session.add(User(email=admin_email, hashed_password=hash_password(admin_password), role="admin"))
                print("✓ Admin user created")

            # ── Providers (from PROVIDER_N_* .env slots) ──────────────────────
            # Each slot defines name, type, base_url, model, and a KEY that names
            # the env var holding the actual API key. Slots whose key is empty or
            # unset are skipped gracefully.
            created_any = False
            for i in range(1, 6):
                name = os.environ.get(f"PROVIDER_{i}_NAME", "")
                if not name:
                    continue
                type_ = os.environ.get(f"PROVIDER_{i}_TYPE", "")
                base_url = os.environ.get(f"PROVIDER_{i}_BASE_URL", "")
                model = os.environ.get(f"PROVIDER_{i}_MODEL", "")
                key_env_var = os.environ.get(f"PROVIDER_{i}_KEY", "")
                if not key_env_var:
                    print(f"  Provider {name}: PROVIDER_{i}_KEY not set — skipping")
                    continue
                if not os.environ.get(key_env_var, ""):
                    print(f"  Provider {name}: env var {key_env_var} is empty — skipping")
                    continue
                params_json = os.environ.get(f"PROVIDER_{i}_PARAMS", "")
                params = json.loads(params_json) if params_json else {}

                existing = (await session.execute(
                    select(Provider).where(Provider.name == name)
                )).scalar_one_or_none()
                if not existing:
                    session.add(Provider(
                        name=name, type=type_, base_url=base_url,
                        model=model, api_key_ref=key_env_var, params=params,
                    ))
                    created_any = True
                    print(f"✓ Provider {name} ({type_}, {model})")
            if not created_any:
                print("  No providers configured — set PROVIDER_1_NAME .. PROVIDER_5_KEY in .env")

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

            # Preset: Hairdresser (was "barber")
            existing = (await session.execute(select(Skill).where(Skill.name == "Hair Salon Front Desk"))).scalar_one_or_none()
            if not existing:
                session.add(Skill(
                    name="Hair Salon Front Desk", preset="hairdresser", language_default="en",
                    playbook_md="## Who you are\nYou are the front desk of a hair salon. You help clients book, cancel, and inquire about appointments and services.\n\n## What you can do\n- Check available slots\n- Book and cancel appointments\n- Describe services (haircut, colour, highlights, treatment, blow-dry)\n- Answer questions about prices, hours, and location\n\n## Tone\nCasual, friendly, like a neighbourhood salon. Suggest a slot proactively.",
                    guardrails_md="Only discuss appointments, services, hours, prices, and location. Do not give hair-care or colouring advice — offer to book a stylist instead. Politely refuse anything else.",
                    scope={"in": ["appointments", "services", "hours", "prices", "location", "haircut", "colour", "color", "highlights", "treatment", "blow-dry", "booking", "salon"], "out_policy": "redirect_then_escalate", "max_offscope_turns": 2},
                    tools=booking_tools(),
                    done_condition="An appointment is confirmed.",
                    escalation={"on": ["explicit_request"], "message": "Let me hand you to a stylist."},
                ))
                print("✓ Hairdresser skill created")

            # Preset: Beautician
            existing = (await session.execute(select(Skill).where(Skill.name == "Beauty Salon Front Desk"))).scalar_one_or_none()
            if not existing:
                session.add(Skill(
                    name="Beauty Salon Front Desk", preset="beautician", language_default="en",
                    playbook_md="## Who you are\nYou are the receptionist of a beauty salon. You help clients book treatments and answer questions about services.\n\n## What you can do\n- Check available slots and book/cancel appointments\n- Describe treatments (facial, manicure, pedicure, waxing, lashes, makeup)\n- Answer questions about prices, duration, hours, and location\n\n## Tone\nWarm, professional, and welcoming.",
                    guardrails_md="Only discuss treatments, prices, hours, location, and booking. Do not give skin-care or medical advice — recommend a consultation. Politely refuse anything else.",
                    scope={"in": ["appointments", "treatments", "facial", "manicure", "pedicure", "waxing", "lashes", "makeup", "prices", "hours", "location", "booking"], "out_policy": "redirect_then_escalate", "max_offscope_turns": 2},
                    tools=booking_tools(),
                    done_condition="An appointment is confirmed.",
                    escalation={"on": ["explicit_request"], "message": "Let me get a beautician to help you."},
                ))
                print("✓ Beautician skill created")

            # Preset: Handyman
            existing = (await session.execute(select(Skill).where(Skill.name == "Handyman Service Desk"))).scalar_one_or_none()
            if not existing:
                session.add(Skill(
                    name="Handyman Service Desk", preset="handyman", language_default="en",
                    playbook_md="## Who you are\nYou are the dispatcher for a home-repair handyman service. You help clients describe their problem, check visit windows, and book a visit.\n\n## What you can do\n- Describe services (plumbing, electrical, painting, carpentry, tiling, general repairs)\n- Give rough price ranges (final quote is on-site)\n- Check available visit windows and book a visit\n- Answer questions about hours and service area\n\n## Tone\nPractical, straightforward, reassuring. Ask what needs fixing if unclear.",
                    guardrails_md="Only discuss services, rough pricing, visit windows, hours, and service area. Do not give DIY instructions or wiring/plumbing advice over chat — book a visit. Politely refuse anything else.",
                    scope={"in": ["plumbing", "electrical", "painting", "carpentry", "tiling", "repairs", "leak", "tap", "switch", "door", "window", "visit", "quote", "hours", "area", "booking"], "out_policy": "redirect_then_escalate", "max_offscope_turns": 2},
                    tools=booking_tools(),
                    done_condition="A visit is booked.",
                    escalation={"on": ["explicit_request"], "message": "Let me get the handyman to call you."},
                ))
                print("✓ Handyman skill created")

            # Preset: Tutor
            existing = (await session.execute(select(Skill).where(Skill.name == "Tutoring Service Desk"))).scalar_one_or_none()
            if not existing:
                session.add(Skill(
                    name="Tutoring Service Desk", preset="tutor", language_default="en",
                    playbook_md="## Who you are\nYou are the assistant for a tutoring service. You help students and parents book sessions and learn about subjects and levels.\n\n## What you can do\n- Describe subjects and levels offered (maths, languages, sciences, exam prep)\n- Check available session slots and book/cancel\n- Answer questions about pricing, materials, and online vs in-person\n\n## Tone\nPatient, encouraging, and clear.",
                    guardrails_md="Only discuss subjects, levels, sessions, materials, pricing, and booking. Do not solve homework or give academic answers over chat — book a session. Politely refuse anything else.",
                    scope={"in": ["tutoring", "subjects", "maths", "languages", "sciences", "exam", "levels", "sessions", "materials", "online", "in-person", "pricing", "booking"], "out_policy": "redirect_then_escalate", "max_offscope_turns": 2},
                    tools=booking_tools(),
                    done_condition="A session is booked.",
                    escalation={"on": ["explicit_request"], "message": "Let me put you in touch with a tutor."},
                ))
                print("✓ Tutor skill created")

            # Preset: Personal Trainer
            existing = (await session.execute(select(Skill).where(Skill.name == "Personal Trainer Desk"))).scalar_one_or_none()
            if not existing:
                session.add(Skill(
                    name="Personal Trainer Desk", preset="personal_trainer", language_default="en",
                    playbook_md="## Who you are\nYou are the assistant for a personal trainer. You help clients book sessions, learn about programmes, and check availability.\n\n## What you can do\n- Describe programmes (strength, weight loss, conditioning, mobility, nutrition guidance)\n- Check available session slots and book/cancel\n- Answer questions about packages, pricing, and location (gym / home / online)\n\n## Tone\nMotivating, energetic, and supportive.",
                    guardrails_md="Only discuss programmes, sessions, packages, pricing, availability, and location. Do not give personalised training or nutrition plans over chat — book a session for an assessment. Politely refuse anything else.",
                    scope={"in": ["training", "fitness", "strength", "weight loss", "conditioning", "mobility", "nutrition", "sessions", "packages", "pricing", "gym", "home", "online", "booking"], "out_policy": "redirect_then_escalate", "max_offscope_turns": 2},
                    tools=booking_tools(),
                    done_condition="A session is booked.",
                    escalation={"on": ["explicit_request"], "message": "Let me get the trainer to contact you."},
                ))
                print("✓ Personal Trainer skill created")

            # Preset: Trainer
            existing = (await session.execute(select(Skill).where(Skill.name == "Youth Sports Trainer"))).scalar_one_or_none()
            if not existing:
                session.add(Skill(
                    name="Youth Sports Trainer", preset="trainer", language_default="pt",
                    playbook_md="## Quem és\nÉs o assistente de um treinador de desporto juvenil. Ajudas os pais com horários, faltas e informações sobre jogos.\n\n## O que podes fazer\n- Consultar o calendário de jogos\n- Registar a falta de um jogador\n- Enviar mensagens aos pais\n\n## Tom\nEncorajador, claro, com espírito de equipa. Fala português de Portugal, trata os pais por 'você'.",
                    guardrails_md="Fala apenas sobre horários, faltas, jogos, treinos e desporto juvenil. Redireciona educadamente qualquer outro assunto.",
                    scope={"in": ["schedule","absences","games","training","teams","players","sports"], "out_policy": "redirect_then_escalate", "max_offscope_turns": 2},
                    tools={"builtin": ["lookup_or_create_contact","close_conversation","escalate_to_human"], "declared": [
                        {"name":"get_schedule","description":"Get the team game schedule","input_schema":{"type":"object","properties":{"team":{"type":"string"}},"required":["team"]},"http":{"method":"GET","url":"http://mock-backend:9000/schedule?team={team}","headers":{},"timeout_s":10,"allowed_hosts":["mock-backend"]},"response_mapping":"json"},
                        {"name":"report_absence","description":"Report a player's absence","input_schema":{"type":"object","properties":{"player_name":{"type":"string"},"date":{"type":"string"},"reason":{"type":"string"}},"required":["player_name","date"]},"http":{"method":"POST","url":"http://mock-backend:9000/absences","headers":{"Content-Type":"application/json"},"timeout_s":10,"allowed_hosts":["mock-backend"]},"response_mapping":"json"},
                    ]},
                    done_condition="The parent's question is answered or the absence is reported.",
                    escalation={"on":["explicit_request"],"message":"Let me get the coach to help you directly."},
                ))
                print("✓ Trainer skill created")

            # ── Demo Bot + Number + Route (optional) ─────────────────────────
            demo_number = os.environ.get("SEED_DEMO_NUMBER", "")
            demo_provider = os.environ.get("SEED_DEMO_PROVIDER", "")
            if demo_number and demo_provider:
                ops_skill = (await session.execute(
                    select(Skill).where(Skill.name == "Operations (Default)")
                )).scalar_one_or_none()
                provider = (await session.execute(
                    select(Provider).where(Provider.name == demo_provider)
                )).scalar_one_or_none()
                if ops_skill and provider:
                    # Number
                    num = (await session.execute(
                        select(Number).where(Number.phone == demo_number)
                    )).scalar_one_or_none()
                    if not num:
                        num = Number(phone=demo_number, display_name="Demo Number")
                        session.add(num)
                        await session.flush()
                        print(f"✓ Demo Number ({demo_number})")

                    # Bot
                    bot = (await session.execute(
                        select(Bot).where(Bot.name == "Demo Bot")
                    )).scalar_one_or_none()
                    if not bot:
                        bot = Bot(
                            name="Demo Bot", skill_id=ops_skill.id,
                            provider_id=provider.id,
                        )
                        session.add(bot)
                        await session.flush()
                        print(f"✓ Demo Bot → Provider: {demo_provider}")

                    # Route
                    route = (await session.execute(
                        select(Route).where(
                            Route.number_id == num.id,
                            Route.bot_id == bot.id,
                        )
                    )).scalar_one_or_none()
                    if not route:
                        session.add(Route(
                            number_id=num.id, bot_id=bot.id,
                            selector="default", priority=0,
                        ))
                        print("✓ Demo Route (Number → Bot)")
                else:
                    if not ops_skill:
                        print("  Demo skipped: skill 'Operations (Default)' not found")
                    if not provider:
                        print(f"  Demo skipped: provider '{demo_provider}' not found")

            await session.commit()
        print(f"\nSeed complete. Login: {admin_email} / (ADMIN_PASSWORD from .env)")

if __name__ == "__main__":
    asyncio.run(seed())
