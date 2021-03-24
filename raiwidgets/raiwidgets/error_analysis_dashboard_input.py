# Copyright (c) Microsoft Corporation
# Licensed under the MIT License.

from .explanation_constants import (ExplanationDashboardInterface,
                                    WidgetRequestResponseConstants)
from scipy.sparse import issparse
from sklearn.feature_selection import mutual_info_classif
import numpy as np
import pandas as pd
import traceback
from .constants import SKLearn
from .error_handling import _format_exception
from ._input_processing import _serialize_json_safe
from erroranalysis._internal.matrix_filter import compute_json_matrix
from erroranalysis._internal.surrogate_error_tree import (
    compute_json_error_tree)


FEATURE_NAMES = ExplanationDashboardInterface.FEATURE_NAMES


class ErrorAnalysisDashboardInput:
    def __init__(
            self,
            explanation,
            model,
            dataset,
            true_y,
            classes,
            features,
            locale,
            categorical_features,
            true_y_dataset):
        """Initialize the ErrorAnalysis Dashboard Input.

        :param explanation: An object that represents an explanation.
        :type explanation: ExplanationMixin
        :param model: An object that represents a model.
        It is assumed that for the classification case
            it has a method of predict_proba() returning
            the prediction probabilities for each
            class and for the regression case a method of predict()
            returning the prediction value.
        :type model: object
        :param dataset: A matrix of feature vector examples
        (# examples x # features), the same samples
            used to build the explanation.
            Will overwrite any set on explanation object already.
            Must have fewer than
            10000 rows and fewer than 1000 columns.
        :type dataset: numpy.array or list[][] or pandas.DataFrame
        :param true_y: The true labels for the provided explanation.
            Will overwrite any set on explanation object already.
        :type true_y: numpy.array or list[]
        :param classes: The class names.
        :type classes: numpy.array or list[]
        :param features: Feature names.
        :type features: numpy.array or list[]
            :param categorical_features: The categorical feature names.
        :type categorical_features: list[str]
        :param true_y_dataset: The true labels for the provided dataset.
        Only needed if the explanation has a sample of instances from the
        original dataset.  Otherwise specify true_y parameter only.
        :type true_y_dataset: numpy.array or list[]
        """
        self._model = model
        original_dataset = dataset
        if isinstance(dataset, pd.DataFrame):
            self._dataset = dataset.to_json()
        else:
            self._dataset = dataset
        if true_y_dataset is None:
            self._true_y = true_y
        else:
            self._true_y = true_y_dataset
        self._categorical_features = categorical_features
        self._string_ind_data = None
        self._categories = []
        self._categorical_indexes = []
        self._is_classifier = model is not None\
            and hasattr(model, SKLearn.PREDICT_PROBA) and \
            model.predict_proba is not None
        self._dataframeColumns = None
        self.dashboard_input = {}
        # List of explanations, key of explanation type is "explanation_type"
        self._mli_explanations = explanation.data(-1)["mli"]
        local_explanation = self._find_first_explanation(
            ExplanationDashboardInterface.MLI_LOCAL_EXPLANATION_KEY)
        global_explanation = self._find_first_explanation(
            ExplanationDashboardInterface.MLI_GLOBAL_EXPLANATION_KEY)
        ebm_explanation = self._find_first_explanation(
            ExplanationDashboardInterface.MLI_EBM_GLOBAL_EXPLANATION_KEY)
        dataset_explanation = self._find_first_explanation(
            ExplanationDashboardInterface.MLI_EXPLANATION_DATASET_KEY)

        if hasattr(explanation, 'method'):
            self.dashboard_input[
                ExplanationDashboardInterface.EXPLANATION_METHOD
            ] = explanation.method

        predicted_y = None
        feature_length = None
        if dataset_explanation is not None:
            if dataset is None or len(dataset) != len(true_y):
                dataset = dataset_explanation[
                    ExplanationDashboardInterface.MLI_DATASET_X_KEY
                ]
            if true_y is None:
                true_y = dataset_explanation[
                    ExplanationDashboardInterface.MLI_DATASET_Y_KEY
                ]
        elif len(dataset) != len(true_y):
            dataset = explanation._eval_data

        if isinstance(dataset, pd.DataFrame) and hasattr(dataset, 'columns'):
            self._dataframeColumns = dataset.columns
        try:
            list_dataset = self._convert_to_list(dataset)
        except Exception as ex:
            ex_str = _format_exception(ex)
            raise ValueError(
                "Unsupported dataset type, inner error: {}".format(ex_str))
        if dataset is not None and model is not None:
            try:
                predicted_y = model.predict(dataset)
            except Exception as ex:
                ex_str = _format_exception(ex)
                msg = "Model does not support predict method for given"
                "dataset type, inner error: {}".format(
                    ex_str)
                raise ValueError(msg)
            try:
                predicted_y = self._convert_to_list(predicted_y)
            except Exception as ex:
                ex_str = _format_exception(ex)
                raise ValueError(
                    "Model prediction output of unsupported type,"
                    "inner error: {}".format(ex_str))

        if classes is None and hasattr(explanation, 'classes')\
                and explanation.classes is not None:
            classes = explanation.classes
        if classes is not None:
            classes = self._convert_to_list(classes)
            self.dashboard_input[
                ExplanationDashboardInterface.CLASS_NAMES
            ] = classes
            class_to_index = {k: v for v, k in enumerate(classes)}

        if predicted_y is not None:
            # If classes specified, convert predicted_y to
            # numeric representation
            if classes is not None and predicted_y[0] in class_to_index:
                for i in range(len(predicted_y)):
                    predicted_y[i] = class_to_index[predicted_y[i]]
            self.dashboard_input[
                ExplanationDashboardInterface.PREDICTED_Y
            ] = predicted_y
        row_length = 0
        if list_dataset is not None:
            row_length, feature_length = np.shape(list_dataset)
            if row_length > 100000:
                raise ValueError(
                    "Exceeds maximum number of rows"
                    "for visualization (100000)")
            if feature_length > 1000:
                raise ValueError("Exceeds maximum number of features for"
                                 " visualization (1000). Please regenerate the"
                                 " explanation using fewer features or"
                                 " initialize the dashboard without passing a"
                                 " dataset.")
            self.dashboard_input[
                ExplanationDashboardInterface.TRAINING_DATA
            ] = _serialize_json_safe(list_dataset)
            self.dashboard_input[
                ExplanationDashboardInterface.IS_CLASSIFIER
            ] = self._is_classifier

        if true_y is not None and len(true_y) == row_length:
            list_true_y = self._convert_to_list(true_y)
            # If classes specified, convert true_y to numeric representation
            if classes is not None and list_true_y[0] in class_to_index:
                for i in range(len(list_true_y)):
                    list_true_y[i] = class_to_index[list_true_y[i]]
            self.dashboard_input[
                ExplanationDashboardInterface.TRUE_Y
            ] = list_true_y

        if local_explanation is not None:
            try:
                local_explanation["scores"] = self._convert_to_list(
                    local_explanation["scores"])
                if np.shape(local_explanation["scores"])[-1] > 1000:
                    raise ValueError("Exceeds maximum number of features for "
                                     "visualization (1000). Please regenerate"
                                     " the explanation using fewer features.")
                local_explanation["intercept"] = self._convert_to_list(
                    local_explanation["intercept"])
                # We can ignore perf explanation data.
                # Note if it is added back at any point,
                # the numpy values will need to be converted to python,
                # otherwise serialization fails.
                local_explanation["perf"] = None
                self.dashboard_input[
                    ExplanationDashboardInterface.LOCAL_EXPLANATIONS
                ] = local_explanation
            except Exception as ex:
                ex_str = _format_exception(ex)
                raise ValueError(
                    "Unsupported local explanation type,"
                    "inner error: {}".format(ex_str))
            if list_dataset is not None:
                local_dim = np.shape(local_explanation["scores"])
                if len(local_dim) != 2 and len(local_dim) != 3:
                    raise ValueError(
                        "Local explanation expected to be a 2D or 3D list")
                if len(local_dim) == 2 and (local_dim[1] != feature_length or
                                            local_dim[0] != row_length):
                    raise ValueError(
                        "Shape mismatch: local explanation"
                        "length differs from dataset")
                if len(local_dim) == 3 and (local_dim[2] != feature_length or
                                            local_dim[1] != row_length):
                    raise ValueError(
                        "Shape mismatch: local explanation"
                        " length differs from dataset")
                if classes is not None and len(classes) != local_dim[0]:
                    raise ValueError("Class vector length mismatch:"
                                     "class names length differs from"
                                     "local explanations dimension")
        if local_explanation is None and global_explanation is not None:
            try:
                global_explanation["scores"] = self._convert_to_list(
                    global_explanation["scores"])
                if 'intercept' in global_explanation:
                    global_explanation["intercept"] = self._convert_to_list(
                        global_explanation["intercept"])
                self.dashboard_input[
                    ExplanationDashboardInterface.GLOBAL_EXPLANATION
                ] = global_explanation
            except Exception as ex:
                ex_str = _format_exception(ex)
                raise ValueError("Unsupported global explanation type,"
                                 "inner error: {}".format(ex_str))
        if ebm_explanation is not None:
            try:
                self.dashboard_input[
                    ExplanationDashboardInterface.EBM_EXPLANATION
                ] = ebm_explanation
            except Exception as ex:
                ex_str = _format_exception(ex)
                raise ValueError(
                    "Unsupported ebm explanation type: {}".format(ex_str))

        if features is None and hasattr(explanation, 'features')\
                and explanation.features is not None:
            features = explanation.features
        if features is not None:
            features = self._convert_to_list(features)
            if feature_length is not None and len(features) != feature_length:
                raise ValueError("Feature vector length mismatch:"
                                 " feature names length differs"
                                 " from local explanations dimension")
            self.dashboard_input[FEATURE_NAMES] = features
        if model is not None and hasattr(model, SKLearn.PREDICT_PROBA) \
                and model.predict_proba is not None and dataset is not None:
            try:
                probability_y = model.predict_proba(dataset)
            except Exception as ex:
                ex_str = _format_exception(ex)
                raise ValueError("Model does not support predict_proba method"
                                 " for given dataset type,"
                                 " inner error: {}".format(ex_str))
            try:
                probability_y = self._convert_to_list(probability_y)
            except Exception as ex:
                ex_str = _format_exception(ex)
                raise ValueError(
                    "Model predict_proba output of unsupported type,"
                    "inner error: {}".format(ex_str))
            self.dashboard_input[
                ExplanationDashboardInterface.PROBABILITY_Y
            ] = probability_y
        if locale is not None:
            self.dashboard_input[ExplanationDashboardInterface.LOCALE] = locale
        if self._categorical_features:
            category_dictionary = {}
            features = self.dashboard_input[FEATURE_NAMES]
            self._categorical_indexes = [features.index(feature) for feature
                                         in self._categorical_features]
            from sklearn.compose import ColumnTransformer
            from sklearn.preprocessing import OrdinalEncoder
            ordinal_enc = OrdinalEncoder()
            ct = ColumnTransformer([('ord', ordinal_enc,
                                     self._categorical_indexes)],
                                   remainder='drop')
            self._string_ind_data = ct.fit_transform(original_dataset)
            transformer_categories = ct.transformers_[0][1].categories_
            for category_arr, category_index in zip(transformer_categories,
                                                    self._categorical_indexes):
                category_values = category_arr.tolist()
                self._categories.append(category_values)
                category_dictionary[category_index] = category_values
            self.dashboard_input[
                ExplanationDashboardInterface.CATEGORICAL_MAP
            ] = category_dictionary

    def debug_ml(self, features, filters, composite_filters):
        try:
            interface = ExplanationDashboardInterface
            feature_names = self.dashboard_input[interface.FEATURE_NAMES]
            if isinstance(self._dataset, str):
                dataset = pd.read_json(self._dataset)
            else:
                dataset = self._dataset
            json_tree = compute_json_error_tree(self._model, dataset,
                                                self._true_y, features,
                                                filters,
                                                composite_filters,
                                                feature_names,
                                                self._categorical_features,
                                                self._categorical_indexes,
                                                self._string_ind_data,
                                                self._categories)
            return {
                WidgetRequestResponseConstants.DATA: json_tree
            }
        except Exception as e:
            print(e)
            traceback.print_exc()
            return {
                WidgetRequestResponseConstants.ERROR:
                    "Failed to generate json tree representation",
                WidgetRequestResponseConstants.DATA: []
            }

    def matrix(self, features, filters, composite_filters):
        try:
            if features[0] is None and features[1] is None:
                return {WidgetRequestResponseConstants.DATA: []}
            interface = ExplanationDashboardInterface
            feature_names = self.dashboard_input[interface.FEATURE_NAMES]
            if isinstance(self._dataset, str):
                dataset = pd.read_json(self._dataset)
            else:
                dataset = self._dataset
            json_matrix = compute_json_matrix(self._model, dataset,
                                              self._true_y,
                                              features, filters,
                                              composite_filters,
                                              feature_names,
                                              self._categorical_features,
                                              self._categories)
            return {
                WidgetRequestResponseConstants.DATA: json_matrix
            }
        except Exception as e:
            print(e)
            traceback.print_exc()
            return {
                WidgetRequestResponseConstants.ERROR:
                    "Failed to generate json matrix representation",
                WidgetRequestResponseConstants.DATA: []
            }

    def importances(self):
        try:
            interface = ExplanationDashboardInterface
            feature_names = self.dashboard_input[interface.FEATURE_NAMES]
            is_pandas = False
            if isinstance(self._dataset, str):
                is_pandas = True
            if is_pandas:
                input_data = pd.read_json(self._dataset)
            else:
                input_data = pd.DataFrame(self._dataset,
                                          columns=feature_names)
            diff = self._model.predict(input_data) != self._true_y
            if is_pandas:
                input_data = input_data.to_numpy()
            if self._categorical_features:
                # Inplace replacement of columns
                for idx, c_i in enumerate(self._categorical_indexes):
                    input_data[:, c_i] = self.string_ind_data[:, idx]
            # compute the feature importances using mutual information
            scores = mutual_info_classif(input_data, diff).tolist()
            return {
                WidgetRequestResponseConstants.DATA: scores
            }
        except Exception as e:
            print(e)
            traceback.print_exc()
            return {
                WidgetRequestResponseConstants.ERROR:
                    "Failed to generate feature importances",
                WidgetRequestResponseConstants.DATA: []
            }

    def on_predict(self, data):
        try:
            if self._dataframeColumns is not None:
                data = pd.DataFrame(data, columns=self._dataframeColumns)
            if (self._is_classifier):
                model_pred_proba = self._model.predict_proba(data)
                prediction = self._convert_to_list(model_pred_proba)
            else:
                model_predict = self._model.predict(data)
                prediction = self._convert_to_list(model_predict)
            return {
                WidgetRequestResponseConstants.DATA: prediction
            }
        except Exception:
            return {
                WidgetRequestResponseConstants.ERROR:
                    "Model threw exception while predicting...",
                WidgetRequestResponseConstants.DATA: []
            }

    def _convert_to_list(self, array):
        if issparse(array):
            if array.shape[1] > 1000:
                raise ValueError("Exceeds maximum number of "
                                 "features for visualization (1000)")
            return array.toarray().tolist()
        if (isinstance(array, pd.DataFrame)):
            return array.values.tolist()
        if (isinstance(array, np.ndarray)):
            return array.tolist()
        return array

    def _find_first_explanation(self, key):
        interface = ExplanationDashboardInterface
        explanation_type_key = interface.MLI_EXPLANATION_TYPE_KEY
        new_array = [explanation for explanation
                     in self._mli_explanations
                     if explanation[explanation_type_key] == key]
        if len(new_array) > 0:
            return new_array[0]["value"]
        return None
