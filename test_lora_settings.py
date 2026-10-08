import unittest
from lora_settings import changed_workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = {'900': {'class_type': 'LoraLoaderModelOnly', 'inputs':
                         {'model': ['13', 0], 'lora_name': 'person.safetensors', 'strength_model': 0.8}},
                         '13': {'class_type': 'LoraLoaderModelOnly', 'inputs':
                         {'model': ['4', 0], 'lora_name': 'addon.safetensors', 'strength_model': 0.8}},
                         '11': {'class_type': 'ModelSamplingAuraFlow', 'inputs': {'model': ['900', 0], 'shift': 3}}}

    def test_disable_addon_preserves_identity_and_connections(self):
        updated = changed_workflow(self.workflow, {'13': {'filename': 'addon.safetensors', 'strength': 0}},
                                   ['person.safetensors', 'addon.safetensors'])
        self.assertEqual(updated['13']['inputs']['strength_model'], 0)
        self.assertEqual(updated['900'], self.workflow['900'])
        self.assertEqual(updated['11'], self.workflow['11'])
        self.assertEqual(self.workflow['13']['inputs']['strength_model'], 0.8)

    def test_missing_file_and_invalid_strength_rejected(self):
        for filename, strength in [('missing.safetensors', 1), ('person.safetensors', -1), ('person.safetensors', float('nan'))]:
            with self.assertRaises(ValueError):
                changed_workflow(self.workflow, {'900': {'filename': filename, 'strength': strength}}, ['person.safetensors'])

    def test_file_switch_preserves_other_nodes(self):
        updated = changed_workflow(self.workflow, {'900': {'filename': 'new.safetensors', 'strength': 1}}, ['new.safetensors'])
        self.assertEqual(updated['900']['inputs']['lora_name'], 'new.safetensors')
        self.assertEqual(updated['13'], self.workflow['13'])


if __name__ == '__main__':
    unittest.main()
