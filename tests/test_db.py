import pytest
from app.db import init_engine, create_all, get_session
from app.models import Service, Template, User, ApiKey, LocalApiKey, Setting


@pytest.mark.asyncio
async def test_db_init_engine(tmp_path):
    db_file = tmp_path / "test.db"
    engine = init_engine(str(db_file))
    
    assert engine is not None
    assert db_file.parent.exists()


@pytest.mark.asyncio
async def test_create_all(initialized_db):
    # Tables should be created without error
    await create_all()
    
    # Should be able to get session
    async with get_session() as session:
        assert session is not None


@pytest.mark.asyncio
async def test_get_session_before_init(tmp_path):
    # Reset global state
    from app import db
    db.SessionLocal = None
    
    with pytest.raises(RuntimeError, match="SessionLocal not initialized"):
        async with get_session() as session:
            pass


@pytest.mark.asyncio
async def test_service_model(initialized_db):
    async with get_session() as session:
        service = Service(
            id="test-svc-1",
            name="Test Service",
            active=True,
            restricted=False,
            message_limit=1000,
            rate_limit=3000
        )
        session.add(service)
        await session.commit()
        
        from sqlalchemy import select
        result = await session.execute(select(Service).where(Service.id == "test-svc-1"))
        retrieved = result.scalar_one()
        
        assert retrieved.id == "test-svc-1"
        assert retrieved.name == "Test Service"
        assert retrieved.active is True
        assert retrieved.message_limit == 1000
        assert retrieved.rate_limit == 3000
        assert retrieved.restricted is False
        assert retrieved.message_limit == 1000


@pytest.mark.asyncio
async def test_template_model(initialized_db):
    async with get_session() as session:
        service = Service(id="svc-1", name="Service", active=True, restricted=False)
        session.add(service)
        await session.commit()
        
        template = Template(
            id="tmpl-1",
            service_id="svc-1",
            name="Welcome Email",
            template_type="email",
            content="Hello ((name))",
            subject="Welcome",
            version=1,
            archived=False,
            hidden=False,
            process_type="normal"
        )
        session.add(template)
        await session.commit()
        
        from sqlalchemy import select
        result = await session.execute(select(Template).where(Template.id == "tmpl-1"))
        retrieved = result.scalar_one()
        
        assert retrieved.id == "tmpl-1"
        assert retrieved.service_id == "svc-1"
        assert retrieved.name == "Welcome Email"
        assert retrieved.template_type == "email"
        assert retrieved.content == "Hello ((name))"
        assert retrieved.subject == "Welcome"
        assert retrieved.version == 1
        assert retrieved.archived is False
        assert retrieved.hidden is False


@pytest.mark.asyncio
async def test_user_model(initialized_db):
    async with get_session() as session:
        user = User(
            id="user-1",
            name="John Doe",
            email_address="john@example.com",
            auth_type="basic",
            mobile_number="+1234567890",
            state="active",
            platform_admin=False,
            blocked=False,
            failed_login_count=0
        )
        session.add(user)
        await session.commit()
        
        from sqlalchemy import select
        result = await session.execute(select(User).where(User.id == "user-1"))
        retrieved = result.scalar_one()
        
        assert retrieved.id == "user-1"
        assert retrieved.name == "John Doe"
        assert retrieved.email_address == "john@example.com"
        assert retrieved.auth_type == "basic"
        assert retrieved.mobile_number == "+1234567890"
        assert retrieved.state == "active"
        assert retrieved.platform_admin is False


@pytest.mark.asyncio
async def test_api_key_model(initialized_db):
    async with get_session() as session:
        api_key = ApiKey(
            id="key-1",
            name="Test Key",
            key_type="normal",
            expiry_date="2025-12-31",
            created_by="user-1",
            created_at="2025-01-01T00:00:00",
            revoked=False,
            version=1
        )
        session.add(api_key)
        await session.commit()
        
        from sqlalchemy import select
        result = await session.execute(select(ApiKey).where(ApiKey.id == "key-1"))
        retrieved = result.scalar_one()
        
        assert retrieved.id == "key-1"
        assert retrieved.name == "Test Key"
        assert retrieved.key_type == "normal"
        assert retrieved.expiry_date == "2025-12-31"
        assert retrieved.created_by == "user-1"
        assert retrieved.revoked is False


@pytest.mark.asyncio
async def test_local_api_key_model(initialized_db):
    async with get_session() as session:
        local_key = LocalApiKey(
            service_id="svc-1",
            key_name="Dev Key",
            key_secret="encrypted-secret",
            key_type="test"
        )
        session.add(local_key)
        await session.commit()
        
        from sqlalchemy import select
        result = await session.execute(select(LocalApiKey))
        retrieved = result.scalar_one()
        
        assert retrieved.service_id == "svc-1"
        assert retrieved.key_name == "Dev Key"
        assert retrieved.key_secret == "encrypted-secret"
        assert retrieved.key_type == "test"


@pytest.mark.asyncio
async def test_setting_model(initialized_db):
    async with get_session() as session:
        setting = Setting(
            key="test_key",
            value="test_value"
        )
        session.add(setting)
        await session.commit()
        
        from sqlalchemy import select
        result = await session.execute(select(Setting).where(Setting.key == "test_key"))
        retrieved = result.scalar_one()
        
        assert retrieved.key == "test_key"
        assert retrieved.value == "test_value"
        assert retrieved.updated_at is not None


@pytest.mark.asyncio
async def test_template_service_relationship(initialized_db):
    async with get_session() as session:
        service = Service(id="svc-1", name="Service", active=True, restricted=False)
        template = Template(
            id="tmpl-1",
            service_id="svc-1",
            name="Template",
            template_type="email",
            content="Content",
            version=1
        )
        session.add(service)
        session.add(template)
        await session.commit()
        
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        result = await session.execute(
            select(Service).where(Service.id == "svc-1").options(selectinload(Service.templates))
        )
        retrieved_service = result.scalar_one()
        
        assert len(retrieved_service.templates) == 1
        assert retrieved_service.templates[0].id == "tmpl-1"
