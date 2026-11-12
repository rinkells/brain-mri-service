from pathlib import Path

import numpy as np
import pytest
import torch
from torchvision.models import resnet18, resnet34

from app.config import CLASS_NAMES
from app.model import BrainMRIClassifier, ModelLoadError, load_model, resolve_device
from tests.helpers import make_image

CPU = torch.device("cpu")
REAL_WEIGHTS = Path(__file__).resolve().parents[1] / "models" / "resnet18_v2_best.pt"


@pytest.fixture(scope="module")
def classifier(weights_path):
    return BrainMRIClassifier.from_file(weights_path, "cpu")


# --- Loading -------------------------------------------------------------------


def test_load_model_restores_saved_weights(weights_path):
    model = load_model(weights_path, CPU)

    saved = torch.load(weights_path, weights_only=True, map_location="cpu")
    assert model.fc.out_features == len(CLASS_NAMES)
    assert all(torch.equal(tensor, saved[name]) for name, tensor in model.state_dict().items())


def test_loaded_model_is_ready_for_inference_only(weights_path):
    model = load_model(weights_path, CPU)

    assert not model.training
    assert not any(p.requires_grad for p in model.parameters())


def test_missing_weights_file(tmp_path):
    with pytest.raises(ModelLoadError, match="not found"):
        load_model(tmp_path / "absent.pt", CPU)


@pytest.mark.parametrize(
    "wrong_model",
    [
        lambda: resnet18(weights=None, num_classes=1000),  # head was not replaced
        lambda: resnet18(weights=None, num_classes=3),  # other number of classes
        lambda: resnet34(weights=None, num_classes=4),  # other architecture
    ],
)
def test_incompatible_state_dict_is_rejected(tmp_path, wrong_model):
    path = tmp_path / "wrong.pt"
    torch.save(wrong_model().state_dict(), path)

    with pytest.raises(ModelLoadError):
        load_model(path, CPU)


def test_pickled_model_object_is_rejected(tmp_path):
    """Only a plain state_dict is accepted: weights_only=True refuses pickled objects."""
    path = tmp_path / "whole_model.pt"
    torch.save(resnet18(weights=None, num_classes=4), path)

    with pytest.raises(ModelLoadError):
        load_model(path, CPU)


def test_garbage_file_is_rejected(tmp_path):
    path = tmp_path / "garbage.pt"
    path.write_bytes(b"not a checkpoint at all")

    with pytest.raises(ModelLoadError):
        load_model(path, CPU)


# --- Device --------------------------------------------------------------------


def test_resolve_cpu():
    assert resolve_device("cpu") == CPU


@pytest.mark.parametrize("name", ["", "gpu", "videocard:0"])
def test_unknown_device_is_rejected(name):
    with pytest.raises(ModelLoadError, match="Unknown DEVICE"):
        resolve_device(name)


@pytest.mark.skipif(torch.cuda.is_available(), reason="CUDA is available on this machine")
def test_cuda_is_rejected_when_unavailable():
    with pytest.raises(ModelLoadError, match="CUDA is not available"):
        resolve_device("cuda")


# --- Inference -----------------------------------------------------------------


def test_predict_returns_probabilities_in_class_order(classifier):
    prediction = classifier.predict(make_image(512, 512, "RGB"))

    assert tuple(prediction.probabilities) == CLASS_NAMES
    assert sum(prediction.probabilities.values()) == pytest.approx(1.0, abs=1e-5)
    assert all(0.0 <= p <= 1.0 for p in prediction.probabilities.values())
    assert prediction.predicted_class == max(
        prediction.probabilities, key=prediction.probabilities.get
    )
    assert prediction.cam is None


def test_predict_matches_manual_forward_pass(classifier):
    image = make_image(640, 480, "RGB")

    with torch.no_grad():
        logits = classifier.model(classifier._transform(image).unsqueeze(0))
    expected = torch.softmax(logits[0], dim=0).tolist()

    assert list(classifier.predict(image).probabilities.values()) == pytest.approx(expected)


def test_predict_is_deterministic(classifier):
    image = make_image(300, 400, "RGB")

    assert classifier.predict(image) == classifier.predict(image)


def test_predict_with_cam_gives_same_probabilities(classifier):
    image = make_image(512, 512, "RGB")

    plain = classifier.predict(image)
    explained = classifier.predict(image, with_cam=True)

    assert explained.predicted_class == plain.predicted_class
    assert list(explained.probabilities.values()) == pytest.approx(
        list(plain.probabilities.values()), abs=1e-6
    )
    assert explained.cam is not None
    assert explained.cam.shape == (7, 7)


def test_class_index_maps_to_class_name(weights_path):
    """Make output i the clear winner and check that class i is reported."""
    model = load_model(weights_path, CPU)
    classifier = BrainMRIClassifier(model, CPU)
    image = make_image(512, 512, "RGB")

    for index, name in enumerate(CLASS_NAMES):
        with torch.no_grad():
            model.fc.weight.zero_()
            model.fc.bias.zero_()
            model.fc.bias[index] = 10.0

        prediction = classifier.predict(image)

        assert prediction.predicted_class == name
        assert prediction.probabilities[name] > 0.99


# --- Real weights (skipped when models/resnet18_v2_best.pt is absent) ----------


@pytest.mark.skipif(not REAL_WEIGHTS.is_file(), reason="real weights are not in models/")
def test_real_weights_load_and_predict():
    classifier = BrainMRIClassifier.from_file(REAL_WEIGHTS, "cpu")

    prediction = classifier.predict(make_image(512, 512, "RGB"), with_cam=True)

    assert tuple(prediction.probabilities) == CLASS_NAMES
    assert sum(prediction.probabilities.values()) == pytest.approx(1.0, abs=1e-5)
    assert prediction.cam.shape == (7, 7)
    assert np.isfinite(prediction.cam).all()
