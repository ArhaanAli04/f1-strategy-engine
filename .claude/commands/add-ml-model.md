# /add-ml-model — Scaffold a new ML model service

Use this when adding a new prediction model to services/ml/.

## What to do

Ask the user:
1. Model name (e.g. overtake_predictor)
2. What it predicts (target variable and type: regression/classification)
3. What input features it uses
4. Which training data it uses (which DB table/query)

Then scaffold all of the following:

### 1. Model service file: services/ml/{model_name}.py
Structure:
```python
class {ModelName}:
    def __init__(self):
        self.model = None  # lazy loaded
        self.model_version = None
    
    def _load_model(self):
        # download from S3 if not cached locally
        # load with joblib
        # set self.model_version from metadata JSON
    
    def predict(self, features: {FeatureSchema}) -> {PredictionSchema}:
        if self.model is None:
            self._load_model()
        # feature engineering
        # model.predict()
        # return typed result
    
    def get_shap_explanation(self, features: {FeatureSchema}) -> list[dict]:
        # SHAP TreeExplainer
        # return top-5 feature contributions as human-readable list
```

### 2. Pydantic schemas in schemas/strategy_schema.py
Add: {ModelName}Features (input) and {ModelName}Prediction (output)
All fields must be typed. No raw dicts.

### 3. Training logic in scripts/train_models.py
Add a train_{model_name}() function that:
- Queries the correct DB table with SQLAlchemy
- Builds the feature matrix with correct column names
- Trains with cross-validation (GroupKFold grouped by session_id)
- Evaluates on holdout set
- Serialises model + feature names + metadata to models/{model_name}.pkl
- Uploads to S3 with version tag

### 4. Unit tests in tests/unit/test_{model_name}.py
Required tests:
- test_predict_returns_correct_type
- test_predict_output_in_reasonable_range
- test_model_loads_from_s3_on_first_call (mock S3 download)
- test_shap_explanation_returns_5_features
- test_missing_feature_raises_value_error

### 5. Register in ML model registry
Add entry to the model registry table in CLAUDE.md.

### 6. Verify
Run: `make test-unit -k test_{model_name}`
All 5 tests must pass before this command is complete.