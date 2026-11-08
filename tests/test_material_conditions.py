import copy
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import condition_syntax as syntax,material_conditions as conditions,native_conditions
from aow4mod.core import FormatError
from test_aow4_mod_audit import mapping,typed,array,u32
from aow4mod.authoring import string


def scalar(name,code,editor,raw):
    definition=typed(0x411694,{40:string(name),44:u32(editor),101:u32(code)})
    prop=typed(0x411696,{40:string(name),100:raw})
    return definition,prop


def bodies():
    flag=scalar('HasColorize',0x80121,0x401607,struct.pack('<4I',1,0,0,0))
    vector=scalar('Color',0x8010c,0x4134c8,struct.pack('<4f',1,1,1,1))
    material=mapping({20:string('[OWN]MAT\\0'),51:array([flag[1],vector[1]])})
    effect=mapping({53:array([flag[0],vector[0]]),63:array([typed(0x41168b,{40:string('ColorizeMask'),41:string('(HasColorize==true)')}),
        typed(0x41168b,{40:string('DynamicFeature'),42:string('(MissingDrawValue>0.0f)')})]),
        51:array([typed(0x41166a,{0:string('OPAQUE'),1:string('')})])})
    return material,effect


class ConditionGrammarTests(unittest.TestCase):
    def test_c_style_precedence_and_parentheses(self):
        a=syntax.parse('A || B && C');self.assertEqual(syntax.evaluate(a,{'A':True,'B':False,'C':False}),1.0)
        self.assertEqual(syntax.evaluate(syntax.parse('(A || B) && C'),{'A':True,'B':False,'C':False}),0.0)

    def test_comparison_and_unary(self):
        self.assertEqual(syntax.evaluate(syntax.parse('!Flag && Value >= -0.5f'),{'Flag':False,'Value':0.25}),1.0)

    def test_float_suffix_is_normalized_to_an_explicit_float_literal(self):
        tree=syntax.parse('(Strength==0.1f)')
        self.assertNotIn('0.1f',syntax.normalized(tree));self.assertIn('.',syntax.normalized(tree))
        self.assertEqual(syntax.evaluate(tree,{'Strength':syntax.f32(0.1)}),1.0)

    def test_float32_literal_rounding_is_explicit(self):
        self.assertEqual(syntax.evaluate(syntax.parse('16777217 == 16777216'),{}),1.0)

    def test_rejects_nonfinite_and_underflow_literals(self):
        for source in ('1e100','1e-100'):
            with self.subTest(source=source),self.assertRaises(FormatError):syntax.parse(source)

    def test_rejects_calls_swizzles_and_arithmetic(self):
        for source in ('abs(X)','X.y','1+2','A & B','A ? B : C',"__import__('os')",'A;B'):
            with self.subTest(source=source),self.assertRaises(FormatError):syntax.parse(source)

    def test_unknown_names_are_not_short_circuited_to_false(self):
        with self.assertRaises(FormatError):syntax.evaluate(syntax.parse('false && Missing'),{'false':False})

    def test_nesting_and_text_bounds(self):
        for source in ('('*34+'1'+')'*34,'A'*2049):
            with self.assertRaises(FormatError):syntax.parse(source)

    def test_empty_pass_condition_is_true(self):
        self.assertEqual(syntax.evaluate(syntax.parse(''),{}),1.0)


class MaterialConditionTests(unittest.TestCase):
    def test_loaded_scalar_types_and_vector_exclusion(self):
        values,unsupported,items=conditions.source_data(*bodies())
        self.assertEqual(values['HasColorize'],{'codec':'bool','value':True});self.assertIn('Color',unsupported)
        self.assertEqual(len(items),3)

    def test_dynamic_conditions_are_deferred_by_default(self):
        values,_,items=conditions.source_data(*bodies());rows,jobs=conditions.jobs_for(values,items)
        self.assertEqual(len(jobs),2);self.assertEqual(rows[1]['state'],'not_evaluated');self.assertIn('draw_context',rows[1]['reason'])

    def test_what_if_still_refuses_unavailable_draw_variables(self):
        values,_,items=conditions.source_data(*bodies());rows,jobs=conditions.jobs_for(values,items,True)
        self.assertEqual(rows[1]['state'],'not_evaluated');self.assertIn('MissingDrawValue',rows[1]['reason'])

    def test_duplicate_material_parameters_are_rejected(self):
        m,e=bodies();flag=scalar('HasColorize',0x80121,0x401607,struct.pack('<4I',1,0,0,0))[1]
        m=mapping({20:string('[OWN]MAT\\0'),51:array([flag,flag])})
        with self.assertRaises(FormatError):conditions.source_data(m,e)

    def test_bool_float_bit_confusion_is_rejected(self):
        flag=scalar('HasColorize',0x80121,0x401607,struct.pack('<4f',1,0,0,0));m=mapping({51:array([flag[1]])});e=mapping({53:array([flag[0]]),51:array([])})
        with self.assertRaises(FormatError):conditions.source_data(m,e)

    def test_no_final_program_or_renderer_selection_claim(self):
        rows=[{'kind':'feature_static','name':'Wind','state':'evaluated','truth':False}]
        decoded={'rows':[{'pass':0,'stage':1,'pass_name':'P','lookup':0,'program_ordinal':0,'program_sha256':'a','descriptor_key':'00'*64,'enabled_features':[],'duplicate_of':None},
                         {'pass':0,'stage':1,'pass_name':'P','lookup':1,'program_ordinal':1,'program_sha256':'b','descriptor_key':'11'*64,'enabled_features':['WIND'],'duplicate_of':None}]}
        with patch.object(conditions.effect_programs,'decode',return_value=decoded):q=conditions.program_candidates({'file':'x','locator':'y'},rows,0,1)
        self.assertEqual(q['candidate_count'],1);self.assertFalse(q['renderer_selection_executed']);self.assertEqual(q['opaque_descriptor_prefix_bytes'],32)

    def test_unevaluated_duplicate_feature_is_not_forced(self):
        rows=[{'kind':'feature_static','name':'Wind','state':'evaluated','truth':False},{'kind':'feature_dynamic','name':'Wind','state':'not_evaluated'}]
        with patch.object(conditions.effect_programs,'decode',return_value={'rows':[]}):q=conditions.program_candidates({'file':'x','locator':'y'},rows,0,1)
        self.assertEqual(q['computed_conditions'],{})


class NativeConditionContractTests(unittest.TestCase):
    def job(self):
        text='Flag==true';return {'source':text,'normalized':syntax.normalized(syntax.parse(text)),
            'variables':{'Flag':{'codec':'bool','value':True},'true':{'codec':'bool','value':True}}}

    def payload(self):
        job=self.job();return {'expression_cid':'00000323','mapping_cid':'00000326','renderer_initialized':False,
            'results':[{'source':job['source'],'normalized':job['normalized'],'parsed':True,'value':1.0,'truth':True,
                        'variables':[{'name':'Flag','code':5,'raw_hex':'01'},{'name':'true','code':5,'raw_hex':'01'}]}]}

    def test_native_result_requires_mapping_value_witnesses(self):
        native_conditions.validate_payload(self.payload(),{'jobs':[self.job()]})

    def test_forged_truth_or_input_bits_are_rejected(self):
        for key in ('truth','value','variables'):
            p=self.payload()
            if key=='truth':p['results'][0]['truth']=False
            elif key=='value':p['results'][0]['value']=0.0
            else:p['results'][0]['variables'][0]['raw_hex']='00'
            with self.assertRaises(FormatError):native_conditions.validate_payload(p,{'jobs':[self.job()]})

    def test_missing_variables_and_wrong_bool_type_are_rejected_before_native(self):
        for mutate in ('missing','type'):
            job=self.job()
            if mutate=='missing':job['variables'].pop('Flag')
            else:job['variables']['Flag']['value']=1
            with self.assertRaises(FormatError):native_conditions.validate_jobs([job])

    def test_zero_jobs_is_not_a_success(self):
        with self.assertRaises(FormatError):native_conditions.validate_jobs([])


if __name__=='__main__':unittest.main()
