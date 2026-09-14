// Hand-authored API copy methods; no generator/build has been executed.
package v1alpha1

import "k8s.io/apimachinery/pkg/runtime"

func (in *FlytGPUProfile) DeepCopy() *FlytGPUProfile {
    if in == nil { return nil }
    out := new(FlytGPUProfile); *out = *in
    in.ObjectMeta.DeepCopyInto(&out.ObjectMeta)
    out.Status.Conditions = append(in.Status.Conditions[:0:0], in.Status.Conditions...)
    return out
}
func (in *FlytGPUProfile) DeepCopyObject() runtime.Object {
    if in == nil { return nil }; return in.DeepCopy()
}
func (in *FlytGPUProfileList) DeepCopyObject() runtime.Object {
    if in == nil { return nil }
    out := new(FlytGPUProfileList); *out = *in
    in.ListMeta.DeepCopyInto(&out.ListMeta)
    if in.Items != nil {
        out.Items = make([]FlytGPUProfile, len(in.Items))
        for i := range in.Items { out.Items[i] = *in.Items[i].DeepCopy() }
    }
    return out
}
func (in *FlytControlPlane) DeepCopy() *FlytControlPlane {
    if in == nil { return nil }
    out := new(FlytControlPlane); *out = *in
    in.ObjectMeta.DeepCopyInto(&out.ObjectMeta)
    out.Status.Conditions = append(in.Status.Conditions[:0:0], in.Status.Conditions...)
    out.Spec.ImagePullSecrets = append(in.Spec.ImagePullSecrets[:0:0], in.Spec.ImagePullSecrets...)
    return out
}
func (in *FlytControlPlane) DeepCopyObject() runtime.Object {
    if in == nil { return nil }; return in.DeepCopy()
}
func (in *FlytControlPlaneList) DeepCopyObject() runtime.Object {
    if in == nil { return nil }
    out := new(FlytControlPlaneList); *out = *in
    in.ListMeta.DeepCopyInto(&out.ListMeta)
    if in.Items != nil {
        out.Items = make([]FlytControlPlane, len(in.Items))
        for i := range in.Items { out.Items[i] = *in.Items[i].DeepCopy() }
    }
    return out
}
func (in *FlytWorker) DeepCopy() *FlytWorker {
    if in == nil { return nil }
    out := new(FlytWorker); *out = *in
    in.ObjectMeta.DeepCopyInto(&out.ObjectMeta)
    out.Status.Conditions = append(in.Status.Conditions[:0:0], in.Status.Conditions...)
    return out
}
func (in *FlytWorker) DeepCopyObject() runtime.Object {
    if in == nil { return nil }; return in.DeepCopy()
}
func (in *FlytWorkerList) DeepCopyObject() runtime.Object {
    if in == nil { return nil }
    out := new(FlytWorkerList); *out = *in
    in.ListMeta.DeepCopyInto(&out.ListMeta)
    if in.Items != nil {
        out.Items = make([]FlytWorker, len(in.Items))
        for i := range in.Items { out.Items[i] = *in.Items[i].DeepCopy() }
    }
    return out
}
