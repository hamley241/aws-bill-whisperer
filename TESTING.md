# Testing Guidelines — AWS Bill Whisperer

## Rule: All Code Must Have Tests

**No code ships without tests.** Every pattern, utility, or feature must have corresponding unit tests.

---

## Test Format: GIVEN-WHEN-THEN

All tests use BDD-style GIVEN-WHEN-THEN format in docstrings:

```python
def test_finds_unattached_volume(self):
    """
    GIVEN: An AWS account with an unattached EBS volume
    WHEN: The pattern scans for unattached volumes
    THEN: It returns a finding with cost estimate
    """
    # GIVEN
    mock_session = MagicMock()
    ...
    
    # WHEN
    findings = pattern.scan()
    
    # THEN
    assert len(findings) == 1
```

---

## Test Requirements per Pattern

Each pattern (`src/patterns/pXXX_*.py`) must have:

| Test Case | Description |
|-----------|-------------|
| **Happy path** | Pattern finds waste when it exists |
| **No finding** | Pattern returns empty when no waste |
| **Edge case** | Boundary conditions (thresholds, limits) |
| **Safety check** | `safe_to_fix` flag is set correctly |
| **Cost calculation** | Monthly cost is calculated accurately |
| **Severity levels** | HIGH/MEDIUM/LOW assigned correctly |

**Minimum: 4 tests per pattern**

---

## Running Tests

```bash
# Install dependencies
cd ~/.openclaw/workspace/projects/aws-bill-whisperer
python3 -m venv venv
source venv/bin/activate
pip install pytest boto3

# Run all tests
pytest tests/ -v

# Run specific pattern tests
pytest tests/test_p001_unattached_ebs.py -v

# Run with coverage
pip install pytest-cov
pytest tests/ --cov=src/patterns --cov-report=term-missing
```

---

## Mocking AWS Calls

**Never make real AWS API calls in tests.** Use `unittest.mock`:

```python
from unittest.mock import MagicMock

mock_session = MagicMock()
mock_ec2 = MagicMock()
mock_session.client.return_value = mock_ec2

# Mock API response
mock_ec2.describe_volumes.return_value = {
    'Volumes': [{'VolumeId': 'vol-123', ...}]
}

# Inject mock into pattern
pattern = UnattachedEBSPattern(session=mock_session)
```

---

## Test File Structure

```
tests/
├── conftest.py              # Shared fixtures
├── test_p001_unattached_ebs.py
├── test_p002_unattached_eip.py
├── test_p003_gp2_to_gp3.py
├── test_p004_idle_ec2.py
├── test_p005_old_snapshots.py
├── test_p006_nat_gateway.py
├── test_p007_idle_rds.py
├── test_base.py             # Base class tests
└── fixtures/                # Test data files
```

---

## Shared Fixtures (conftest.py)

```python
@pytest.fixture
def mock_session():
    return MagicMock()

@pytest.fixture
def old_date():
    return datetime.now(timezone.utc) - timedelta(days=100)
```

---

## CI Integration

Tests run automatically on every PR. To pass:

1. All tests must pass
2. No decrease in coverage
3. `ruff check src/` must pass (linting)

---

## Adding a New Pattern

When adding `pXXX_new_pattern.py`:

1. Create pattern in `src/patterns/`
2. Create `tests/test_pXXX_new_pattern.py` with 4+ tests
3. Follow GIVEN-WHEN-THEN format
4. Run `pytest tests/ -v` locally
5. Submit PR

**PRs without tests will not be merged.**
